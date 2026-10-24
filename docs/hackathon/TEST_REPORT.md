# Reasoning Engine — Test Report (Audit)

| Field | Value |
|-------|-------|
| Date | 2026-10-24 |
| Branch | `hackathon/reasoning-engine` |
| Commit tested | `89c4d9d` (`89c4d9dc3621ef7fa021fe3852994110876a7799`) |
| Scope | Audit + real offline/SAMPLE_MODE tests only — **no new product features** |
| Live Zetaris / Meterless / NVIDIA calls | **None** (no real credentials used) |

Uncommitted artifacts produced by this audit (not committed yet):

- `backend/apps/reasoning_engine/tests/` (new)
- `output_examples/verified_example.json`
- `docs/hackathon/TEST_REPORT.md` (this file)

---

## 1. Git state at audit start

```text
$ git branch --show-current
hackathon/reasoning-engine

$ git status --porcelain
(empty — clean tree)

$ git log --oneline -8
89c4d9d docs(hackathon): add HACKATHON_README, golden traces and sample inputs/outputs
fe710b9 feat(reasoning-engine): implement NVIDIA Nemotron adapter with health check, validation and direct fallback
87d0331 feat(reasoning-engine): implement Meterless Markovian orchestration with Planner/Writer/Evaluator loop and bounded revisions
119da84 feat(reasoning-engine): implement Zetaris Lightning SQL adapter for grant context
d70356c feat(reasoning-engine): scaffold agentic grant writer with Planner/Writer/Evaluator loop, SAMPLE_MODE and JSONL traces
231c57b fix(whatsapp): state machine inbound 100% no Django (D-038 / R-013)
4379238 fix(growth): rescore leads com rubrica e fallback de modelo no scorer
1c09734 fix(ai): migra fallback Groq de Llama 3.3 deprecado para gpt-oss-120b
```

---

## 2. Code reality (file summaries)

### `services/orchestrator.py`
Bounded loop coordinator. Reads `SAMPLE_MODE` from env only inside this app. Flow: Zetaris context → Planner seed → Meterless loop; on Meterless failure in live mode, NVIDIA direct fallback. Errors return structured JSON (`status=error`, `error.type/message/recoverable`) — no traceback in payload. Caps revisions at ≤3.

### `adapters/zetaris_adapter.py`
SAMPLE_MODE → `sample_data.get_sample_grant_context` (no network). Live → `POST {ZETARIS_BASE_URL}{ZETARIS_QUERY_PATH}` default path `/api/v1.0/sql-editor/sqls/run-query`. Multiple response-shape normalizers. **Endpoint/schema not validated against a live Zetaris tenant in this audit.**

### `adapters/meterless_adapter.py`
SAMPLE_MODE → local Planner→Writer⇄Evaluator loop (no sockets). Live → `POST {METERLESS_BASE_URL}/api/v1/engines/markovian/run` (default). Payload/response mapping is defensive/multi-shape. **Markovian path is assumed; not confirmed against real Nightshift docs/API in this audit.**

### `adapters/nvidia_adapter.py`
SAMPLE_MODE → heuristic text / catalog. Live → OpenAI-compatible NIM (`GET /models`, `POST /chat/completions`) at `https://integrate.api.nvidia.com/v1` by default. Used as Meterless fallback. **Never exercised with a real `NVIDIA_API_KEY` here.**

### Agents
- **Planner**: deterministic outline; angles from portfolio tokens.
- **Writer**: SAMPLE drafts vary by portfolio/language/fingerprint; live non-sample path is a placeholder string pending Meterless/NVIDIA.
- **Evaluator**: SAMPLE forces score 6.5 on revision 0 (visible revise cycle); then heuristic coverage scoring; `needs_revision` if below target.

### `services/tracer.py`
JSONL to `logs/trace-<run_id>.jsonl` + stdout; redacts secret-ish keys; truncates summaries.

### `tests/test_smoke.py`
**ABSENT** — file does not exist on this branch. Only audit tests under `tests/test_sample_mode_isolated.py` were added.

---

## 3. Adapter classification (honest)

| Adapter | Classification | Notes |
|---------|----------------|-------|
| **Zetaris** (live HTTP) | **IMPLEMENTED_UNVERIFIED** | Client code exists; default SQL-editor path/schema **unverified** against real API. Not “integrado”. |
| **Zetaris** (SAMPLE_MODE) | **SAMPLE_ONLY** | Synthetic grants; verified offline in this audit. |
| **Meterless** (Nightshift) | **IMPLEMENTED_UNVERIFIED** | Client + assumed `/api/v1/engines/markovian/run`; **unverified**. Not “integrado”. |
| **Meterless** (SAMPLE local loop) | **SAMPLE_ONLY** | Full bounded loop offline; verified. |
| **NVIDIA** (NIM live) | **IMPLEMENTED_UNVERIFIED** | OpenAI-compatible client; **no live credential call**. Not “integrado”. |
| **NVIDIA** (SAMPLE_MODE) | **SAMPLE_ONLY** | Heuristic generate / static catalog. |

None of the adapters are **STUB** (they do not deliberately return a canned “not implemented” error when configured). None are **IMPLEMENTED_REAL** under this audit’s evidence standard (no confirmed docs + no successful authenticated call).

---

## 4. Commands executed

Isolation rules: did **not** source `infra/.env.production`; overrode `DJANGO_SECRET_KEY` via env; cleared provider keys; avoided production Postgres.

```bash
# --- unit tests (venv python; system `python` absent) ---
export PATH="/opt/klio/.venv/bin:$PATH"
export DJANGO_SECRET_KEY="$(openssl rand -hex 32)"
export DATABASE_URL="sqlite:////tmp/klio_re_audit/test.sqlite3"   # see blockage note
export DEMO_MODE=false DEBUG=true ALLOWED_HOSTS=localhost,127.0.0.1
export SAMPLE_MODE=true REASONING_ENGINE_ENABLED=true
unset ZETARIS_API_KEY ZETARIS_BASE_URL METERLESS_RUNTIME_TOKEN METERLESS_BASE_URL NVIDIA_API_KEY
cd /opt/klio/backend
python manage.py test apps.reasoning_engine --verbosity 2

# --- HTTP (SQLite file forced to /tmp via temporary symlink; NO DATABASE_URL) ---
# backend/db.sqlite3 briefly → /tmp/klio_re_audit/http.sqlite3, then restored
export DJANGO_SECRET_KEY="$(openssl rand -hex 32)"
unset DATABASE_URL
export SAMPLE_MODE=true REASONING_ENGINE_ENABLED=true DEMO_MODE=false DEBUG=true
unset ZETARIS_API_KEY ZETARIS_BASE_URL METERLESS_RUNTIME_TOKEN METERLESS_BASE_URL NVIDIA_API_KEY
python manage.py migrate --run-syncdb
python manage.py runserver 127.0.0.1:8002 --noreload
curl -sS -X POST http://127.0.0.1:8002/api/reasoning_engine/draft/ \
  -H 'Content-Type: application/json' \
  -d '{"grant_id":"sample-grant-cultura-sp","user_portfolio_text":"...","output_language":"pt-BR"}' \
  > /opt/klio/output_examples/verified_example.json

# --- secret scan ---
grep -RIn --exclude-dir=.git --exclude-dir=node_modules \
  -E "(sk-[A-Za-z0-9]{10}|nvapi-[A-Za-z0-9]{10}|aact_)" \
  backend/apps/reasoning_engine/ logs/ input_examples/ output_examples/
```

### Settings blockage (not fixed — out of scope)

Setting `DATABASE_URL=sqlite:////tmp/...` hits `config/settings/base.py`, which always injects Postgres `OPTIONS["options"]=statement_timeout` whenever `DATABASE_URL` is set. Result:

```text
TypeError: 'options' is an invalid keyword argument for Connection()
```

Per audit rules, **infra/settings were not patched**. Workaround for HTTP: unset `DATABASE_URL` and temporarily point `backend/db.sqlite3` at a `/tmp` file via symlink (restored after the run). Unit tests used `SimpleTestCase` and skipped DB setup, so they still passed with the broken `DATABASE_URL`.

---

## 5. Raw test output (summary; tracer JSONL truncated)

Full capture: `/tmp/klio_re_audit/test_output.txt` (~293 lines; mostly tracer stdout).

```text
Found 5 test(s).
Skipping setup of unused database(s): default.
System check identified no issues (0 silenced).
test_bounded_loop_stops_at_three_revisions ... ok
test_live_mode_without_credentials_returns_structured_error ... ok
test_no_hardcoded_outputs_language_variation ... ok
test_no_hardcoded_outputs_portfolio_variation ... ok
test_sample_mode_full_loop_offline ... ok

----------------------------------------------------------------------
Ran 5 tests in 0.037s

OK
EXIT_CODE=0
```

---

## 6. Execution time

| Measurement | Value |
|-------------|-------|
| Django test suite (`manage.py test apps.reasoning_engine`) | **0.037s** wall (suite) |
| Orchestrator SAMPLE loop (`execution_time_seconds` in response) | **0.003s** |
| Orchestrator SAMPLE loop (perf_counter wall) | **~0.003s** |
| HTTP POST wall (`curl` → draft) | **~0.016s** |
| HTTP `execution_time_seconds` | **0.003s** |

SAMPLE_MODE is local heuristics — sub-second is expected. Live Meterless/NVIDIA latency remains **unverified**.

---

## 7. What passed

| Check | Result |
|-------|--------|
| SAMPLE_MODE full loop offline (no network / no provider keys) | PASS |
| `status ∈ {success, needs_review}`, `run_id`, non-empty draft | PASS |
| ≥3 agents, score 0–10, revisions ≤3, `mode=sample` | PASS |
| JSONL log ≥10 lines, each valid JSON, has `evaluate` + handoff/target | PASS |
| Portfolio variation → distinct drafts | PASS |
| Language en/es/pt-BR → distinct drafts | PASS |
| Forced low Evaluator → exactly 3 revisions + `needs_review` | PASS |
| `SAMPLE_MODE=false` without keys → structured `status=error` (no traceback) | PASS |
| HTTP `POST /api/reasoning_engine/draft/` on `127.0.0.1:8002` | PASS (200) |
| Secret scan (`sk-` / `nvapi-` / `aact_`) in RE paths | PASS (no matches) |

---

## 8. What failed / blocked

| Item | Result |
|------|--------|
| `test_smoke.py` | **Missing** (N/A) |
| Live Zetaris / Meterless / NVIDIA | **Not run** (no credentials; by design) |
| `DATABASE_URL=sqlite:///tmp/...` via current settings | **Blocked** (PG OPTIONS on sqlite) — not fixed |
| Claiming adapters as “integrados” | **Refused** — they are **IMPLEMENTED_UNVERIFIED** |

---

## 9. Still unverified

- Zetaris Lightning SQL editor path, auth headers (`X-Org-ID`), and response shapes against a real tenant.
- Meterless Nightshift Markovian endpoint path, request/response contract, and headers (`meterless-app`, `meterless-scope`).
- NVIDIA NIM model IDs (`NEMOTRON_MODEL`) and chat-completions behavior with a real key.
- End-to-end live fallback path Orchestrator → Meterless fail → NVIDIA direct.
- Latency/cost under live sponsors.

Local README / HACKATHON_README document assumed payloads; that documentation itself is **unverified** against vendor sources.

---

## 10. Risks for a judge on a fresh clone

1. Needs Python venv with Django (`/opt/klio/.venv` here); bare `python` may be missing — use `python3` from venv.
2. `DJANGO_SECRET_KEY` required; `backend/.env` may exist on a machine but should not be assumed in a clean clone.
3. `REASONING_ENGINE_ENABLED=true` and `SAMPLE_MODE=true` must be set or routes/loop won’t behave as demoed.
4. Tracer writes under repo `logs/` and prints JSONL to stdout (noisy under `--verbosity 2`).
5. Without SAMPLE_MODE and without keys, API returns structured **error** (ZetarisNotConfigured) — correct, but not a draft demo.
6. `DATABASE_URL` pointing at sqlite breaks under current settings OPTIONS injection.
7. Live sponsor demos will fail until real credentials + verified endpoints exist.
8. Golden examples under `output_examples/reasoning_engine/` are SAMPLE-generated; `verified_example.json` from this audit is also SAMPLE_ONLY.

---

## 11. Secret scan

```text
grep … (sk-|nvapi-|aact_) → no matches (exit 1 / empty)
```

No secrets found in `backend/apps/reasoning_engine/`, `logs/`, `input_examples/`, `output_examples/`.

---

## 12. Recommendation (next step)

1. Obtain **official** Meterless Nightshift + Zetaris docs (or a sandbox) and mark endpoints verified before any “live integration” claim.
2. Add a thin **live smoke** behind explicit env flags (still optional for judges) once credentials exist — do not invent paths.
3. Optionally fix sqlite-via-`DATABASE_URL` OPTIONS gating in settings (small, out of this audit) so `/tmp` SQLite works without symlink hacks.
4. Keep SAMPLE_MODE as the default judge path; commit the new isolated tests when ready.
