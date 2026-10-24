# Reasoning Engine (Open Agent Hackathon 2026)

Isolated Django app that runs a bounded **Markovian** loop — **Planner → Writer ⇄ Evaluator** (max 3 revisions, target score 8.0) — to draft grant applications.

| Layer | Sponsor | Role |
|-------|---------|------|
| Data | **Zetaris** | Lightning SQL over REST — grant rubric / eligibility (read-only) |
| Orchestration | **Meterless Nightshift** | Markovian engine runtime — agent handoffs, critiques, retries |
| Compute | **NVIDIA / Nemotron** | Primary via Meterless `config.model`; `NvidiaAdapter` for health/validation/direct fallback |

## Implementation status (honest)

Labels: **IMPLEMENTED + TESTED** · **IMPLEMENTED_UNVERIFIED** · **SAMPLE / LOCAL ONLY** · **NOT IMPLEMENTED**

| Surface | Status | Evidence |
|---------|--------|----------|
| SAMPLE_MODE full loop (Planner→Writer⇄Evaluator, ≤3 revisions) | **IMPLEMENTED + TESTED** | `tests/test_sample_mode_isolated.py`, offline HTTP draft |
| Local project memory + provenance + resume/facts API | **IMPLEMENTED + TESTED** | `tests/test_memory.py`; no production Postgres required (`memory` backend or SQLite `/tmp`) |
| JSONL tracer / structured errors without credentials | **IMPLEMENTED + TESTED** | sample + live-missing-keys tests |
| Zetaris live HTTP client | **IMPLEMENTED_UNVERIFIED** | Code exists; **no official contract in repo**; **never called with real keys** |
| Meterless Nightshift Markovian live HTTP | **IMPLEMENTED_UNVERIFIED** | Code exists; path/payload **assumed**; **not “integrado”**; **no live test** |
| NVIDIA NIM live (`/models`, `/chat/completions`) | **IMPLEMENTED_UNVERIFIED** | OpenAI-compatible client assumed; **no live key test** |
| Zetaris / Meterless / NVIDIA under `SAMPLE_MODE` | **SAMPLE / LOCAL ONLY** | Synthetic / heuristic; no sponsor network |
| Meterless H-MEM remote sync | **NOT IMPLEMENTED** | Local persistence only; remote refused until official API |

Full gap checklist (URL, auth, schemas, retries, rate limits, how to get test creds):  
→ [`CONTRACT_GAPS.md`](./CONTRACT_GAPS.md)

**Do not** describe Meterless (or Zetaris/NVIDIA live) as “integrated” until `CONTRACT_GAPS.md` rows are filled from **official** docs and a live smoke passes.

## Feature flags

| Env | Default | Effect |
|-----|---------|--------|
| `REASONING_ENGINE_ENABLED` | `false` | Mounts `POST /api/reasoning_engine/draft/` |
| `SAMPLE_MODE` | `false` | Synthetic grant context + **local** Markovian loop; **no** network to Zetaris/Meterless/NVIDIA |
| `REASONING_ENGINE_MAX_REVISIONS` | `3` | Hard cap on Writer revisions |
| `REASONING_ENGINE_TARGET_SCORE` | `8.0` | Evaluator accept threshold |
| `REASONING_ENGINE_MEMORY_BACKEND` | `memory` in SAMPLE else `django` | Local memory store |
| `REASONING_ENGINE_MEMORY_MAX_CHARS` | `1200` | Bounded recall budget |
| `METERLESS_HMEM_ENABLED` | `false` | Remote H-MEM — leave false until official contract |

`SAMPLE_MODE` is read only inside this app. It never writes fake editais into the Klio radar DB.

## Markovian loop

```
owner_key? → MemoryService (recall / upsert facts)
        ↓
Zetaris.fetch_grant_context
        ↓
Planner (seed outline + confirmed facts)
        ↓
Meterless.run_agent_loop
  Planner → Writer → Evaluator
                 ↑ critique │
                 └─ revise ─┘  (≤ 3)
        ↓
save_revision + JSONL trace (+ HMemAdapter local sync note)
```

- **success** when `score >= target_score`
- **needs_review** when cap reached with score still below target
- Every handoff is logged to `logs/trace-<run_id>.jsonl` + stdout

## Zetaris (grant context)

**Status:** live client = **IMPLEMENTED_UNVERIFIED** · SAMPLE = **SAMPLE / LOCAL ONLY**.  
Official contract: **not in this repo** — see [`CONTRACT_GAPS.md`](./CONTRACT_GAPS.md) §1.

Read-only intent. Env knobs in `.env.example` (`ZETARIS_*`) are **placeholders / assumptions**, not a verified vendor guide.  
If credentials are missing and `SAMPLE_MODE=false` → structured `ZetarisNotConfigured`.

## Meterless (Markovian orchestration)

**Status:** Nightshift live HTTP = **IMPLEMENTED_UNVERIFIED** — **not integrated**.  
SAMPLE local loop = **SAMPLE / LOCAL ONLY** (tested).  
Official Markovian contract: **not in this repo** — see [`CONTRACT_GAPS.md`](./CONTRACT_GAPS.md) §2.

### Configure (assumed env — unverified)

```bash
METERLESS_BASE_URL=http://127.0.0.1:8088          # assumed host — confirm with Meterless
METERLESS_RUNTIME_TOKEN=your-token-here           # never commit real tokens
METERLESS_MARKOVIAN_PATH=/api/v1/engines/markovian/run   # ASSUMED path — unverified
METERLESS_TIMEOUT_SECONDS=60
METERLESS_STEP_TIMEOUT_SECONDS=90
METERLESS_MAX_ATTEMPTS=2
METERLESS_APP_ID=klio-reasoning-engine
NEMOTRON_MODEL=your-model-name
```

If `METERLESS_BASE_URL` or `METERLESS_RUNTIME_TOKEN` is missing (and `SAMPLE_MODE` is false) →  
`MeterlessNotConfigured` with recoverable=false.

### Assumed Nightshift payload (UNVERIFIED — not an official schema)

The adapter currently POSTs to `{METERLESS_BASE_URL}{METERLESS_MARKOVIAN_PATH}` with a body shaped like `engine` / `plan` / `context` / `agents` / `config`, plus headers `Authorization: Bearer …`, `meterless-app`, `meterless-scope`.  
**These shapes are code assumptions.** Do not treat them as Meterless documentation. Replace when an official contract arrives (`CONTRACT_GAPS.md`).

### SAMPLE_MODE local loop

When `SAMPLE_MODE=true`, `MeterlessAdapter` **does not** open sockets. It runs:

1. `PlannerAgent.run` → outline (angles derived from portfolio tokens)
2. `WriterAgent.run` → draft (portfolio + requirements; fingerprint varies by input)
3. `EvaluatorAgent.run` → score/critique (`target_score` default 8.0)
4. On first pass, Evaluator forces a revision for observability; Writer incorporates critique keywords; second evaluation typically reaches ≥ 8.0

Provider field in JSONL: `local_sample`.

### Errors

| Condition | Type | Recoverable |
|-----------|------|-------------|
| Missing base URL / token | `MeterlessNotConfigured` | false |
| HTTP 401/403 | `MeterlessAuthError` | false |
| HTTP 404 | `MeterlessNotFound` | false |
| Timeout (60s) | `MeterlessTimeout` | true |
| 5xx after retries | `MeterlessRuntimeError` | true |

API response shape on failure:

```json
{
  "status": "error",
  "error": {
    "type": "MeterlessNotConfigured",
    "message": "Meterless credentials not configured. Set METERLESS_BASE_URL and METERLESS_RUNTIME_TOKEN.",
    "recoverable": false
  },
  "trace_id": "...",
  "log_file": "logs/trace-<run_id>.jsonl"
}
```

## NVIDIA Nemotron (health + direct fallback)

**Status:** live NIM client = **IMPLEMENTED_UNVERIFIED** · SAMPLE heuristics = **SAMPLE / LOCAL ONLY**.  
No NVIDIA OpenAPI/PDF checked into this repo — see [`CONTRACT_GAPS.md`](./CONTRACT_GAPS.md) §4.

NVIDIA is intended **primarily through Meterless Nightshift** (`config.model = NEMOTRON_MODEL`) once that contract is verified.  
`NvidiaAdapter` is a separate client for:

1. **health_check** — NIM connectivity / latency  
2. **validate_model** — confirm `NEMOTRON_MODEL` exists  
3. **list_models** — debug/demo catalog (filters Nemotron / reasoning)  
4. **generate_text** — **direct fallback** if Meterless fails but NIM is healthy  

### Configure

```bash
NVIDIA_API_KEY=your-key-here
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
NEMOTRON_MODEL=nvidia/nemotron-4-340b-instruct
NVIDIA_TIMEOUT_SECONDS=30
NVIDIA_MAX_ATTEMPTS=2
```

Known / sample catalog models (non-exhaustive):

- `nvidia/nemotron-4-340b-instruct`
- `nvidia/llama-3.1-nemotron-ultra-253b-v1`
- `nvidia/llama-3.3-nemotron-super-49b-v1`
- `nvidia/nemotron-3-super-120b-a12b`
- `nvidia/nemotron-3-nano-30b-a3b`

### Direct chat payload

`POST {NVIDIA_BASE_URL}/chat/completions`

```json
{
  "model": "nvidia/nemotron-4-340b-instruct",
  "messages": [{"role": "user", "content": "..."}],
  "temperature": 0.7,
  "max_tokens": 1024,
  "stream": false
}
```

Headers: `Authorization: Bearer {NVIDIA_API_KEY}`.

### When fallback is triggered

In `SAMPLE_MODE=false`, if `MeterlessAdapter.run_agent_loop` raises  
`MeterlessRuntimeError` / `MeterlessTimeout` / `MeterlessNotFound` / `MeterlessNotConfigured`:

1. Orchestrator logs `action=meterless_fallback`
2. Calls `NvidiaAdapter.health_check()`
3. If `status=ok`, runs `_run_with_nvidia_direct_fallback` (local Planner + NVIDIA Writer/Evaluator, max 3 revisions, `provider=nvidia_direct`)
4. If NVIDIA unhealthy, re-raises the original Meterless error

`SAMPLE_MODE=true` never calls NVIDIA (local heuristics only).

## How to test

### SAMPLE_MODE (offline judges)

```bash
cd backend
export REASONING_ENGINE_ENABLED=true SAMPLE_MODE=true
export DJANGO_SECRET_KEY=dev-only DEBUG=true
export DATABASE_URL=sqlite:////tmp/klio-reasoning-test.sqlite3

../.venv/bin/python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from apps.reasoning_engine.services.orchestrator import ReasoningOrchestrator
r = ReasoningOrchestrator().run(
    grant_id='sample-grant-cultura-sp',
    user_portfolio_text='Coletivo de teatro com circulação, oficinas e audiodescrição em SP.',
    output_language='pt-BR',
)
print(r['status'], r['output']['score'], r['output']['revisions'], r['execution_time_seconds'])
print(r['log_file'])
"
```

Expect: `status=success`, `score >= 8.0`, `revisions >= 1`, JSONL with Planner/Writer/Evaluator handoffs.

### Live without Meterless credentials

Keep Zetaris mocked or configured, leave Meterless unset:

```bash
SAMPLE_MODE=false
# → error.type == MeterlessNotConfigured (after Zetaris succeeds)
```

### Live with Nightshift

```bash
export SAMPLE_MODE=false
export METERLESS_BASE_URL=http://127.0.0.1:8088
export METERLESS_RUNTIME_TOKEN=...   # from Nightshift cockpit / proxy-token
export ZETARIS_BASE_URL=...
export ZETARIS_API_KEY=...
# orchestrator.run(...)
```

Inspect JSONL for `provider=meterless` and `action=delegate` / agent iterations.

## Persistent project memory (session resume)

**Problem:** a cultural producer starts a proposal, pauses, and returns days later.
A stateless orchestrator forgets confirmed facts and re-asks everything — or worse,
hallucinates awards/partnerships that were never established.

**What we implemented (local):**

| Piece | Role |
|-------|------|
| `models.GrantProject` / `ProjectFact` / `DraftRevision` / `AgentDecision` | Local schema with provenance |
| `services/memory_service.py` | get_or_create, upsert, bounded recall, revisions, decisions |
| `adapters/hmem_adapter.py` | Interface for sync/restore/health — **local only** for now |

> **local persistence implemented; H-MEM remote sync pending official API contract**  
> We do **not** invent a Meterless H-MEM HTTP endpoint. Live remote sync raises a
> controlled `HMemRemoteContractPending` / returns an explicit pending message.

### Provenance & anti-hallucination

- Every `ProjectFact` stores `source` (`user_input` | `portfolio` | `grant_rubric` | `agent_inference`),
  `confidence`, `confirmed_by_user`, and timestamps.
- Writer may **assert** only facts with `confirmed_by_user=True` (typically `user_input`).
- Unconfirmed inferences appear as `[PENDENTE: …]` — never as truth.
- Awards, partnerships, ofícios, approved budgets, CNPJ/CPF/addresses are blocked
  unless confirmed by the user.
- PII (email, phone, CPF, CNPJ, tokens) is **rejected** at upsert time.
- `recall_facts` is **bounded** (default ~1200 chars): confirmed facts first; overflow logged as dropped.

### SAMPLE_MODE memory

- Default backend: **in-process memory** (`REASONING_ENGINE_MEMORY_BACKEND=memory`) — no Postgres, no network.
- Optional durable local: `REASONING_ENGINE_MEMORY_BACKEND=django` + `DATABASE_URL=sqlite:////tmp/...`.

### Endpoints

`POST /api/reasoning_engine/draft/` (only when `REASONING_ENGINE_ENABLED=true`)

```json
{
  "grant_id": "sample-grant-cultura-sp",
  "user_portfolio_text": "...",
  "output_language": "pt-BR",
  "owner_key": "opaque-user-key",
  "resume_session": true,
  "confirmed_facts": {"coletivo.anos_atuacao": "5"}
}
```

`GET /api/reasoning_engine/projects/<grant_id>/resume/?owner_key=opaque-user-key`

```json
{
  "project_id": "...",
  "status": "drafting|paused|needs_review|ready",
  "current_version": 3,
  "confirmed_facts": {"coletivo.anos_atuacao": "5"},
  "pending_questions": ["[PENDENTE: ...]"],
  "latest_draft_excerpt": "..."
}
```

`POST /api/reasoning_engine/projects/<project_id>/facts/`

```json
{ "key": "coletivo.anos_atuacao", "value": "5", "confirmed": true }
```
