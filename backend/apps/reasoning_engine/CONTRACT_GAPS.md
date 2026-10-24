# Contract gaps — sponsor adapters (official docs missing)

**Date:** 2026-10-24  
**Branch:** `hackathon/reasoning-engine`  
**Method:** Local-only search of `docs/`, `HACKATHON_README.md`, `backend/apps/reasoning_engine/README.md`, `.env.example`, adapter source comments, and `docs/hackathon/TEST_REPORT.md`.

## Verdict

| Sponsor surface | Official contract in this repo? | Live call with real credentials? | Status label |
|-----------------|----------------------------------|----------------------------------|--------------|
| Zetaris Lightning SQL / SQL-editor | **No** | **No** | `IMPLEMENTED_UNVERIFIED` (code assumes paths) |
| Meterless Nightshift Markovian | **No** | **No** | `IMPLEMENTED_UNVERIFIED` — **not “integrado”** |
| Meterless H-MEM remote | **No** | **No** | **Not implemented** (local memory only; remote refused) |
| NVIDIA NIM / Nemotron | **No vendor PDF/OpenAPI in repo** | **No** | `IMPLEMENTED_UNVERIFIED` (OpenAI-compatible shape assumed) |

**Rule followed:** no new fictional live calls were added. Values below under “Currently assumed in code” are **not** confirmed contracts — they are what the adapters already encode and must be replaced or deleted once official docs arrive.

Evidence that local docs are **not** official vendor contracts:

- Paths/payloads appear first in adapter defaults / `.env.example`, then are **copied into** README / HACKATHON_README (circular).
- `docs/hackathon/TEST_REPORT.md` already classifies live adapters as unverified and records **zero** live sponsor calls.
- No OpenAPI, Postman collection, sponsor kit, or signed API guide for Zetaris / Meterless / NVIDIA lives under `docs/` or `backend/apps/reasoning_engine/`.

---

## 1. Zetaris (grant context / Lightning SQL)

### What we need (official)

| Gap field | Status |
|-----------|--------|
| **URL base oficial** | **MISSING.** Code default `https://api.enterprise.zetaris.com` is **unverified**. Need tenant/region base from Zetaris docs or console. |
| **Método e path** | **MISSING.** Code assumes `POST /api/v1.0/sql-editor/sqls/run-query` (also `open_sql` / `generic` modes via env). Need confirmed SQL-over-REST path(s). |
| **Autenticação (header, scope)** | **MISSING.** Code sends `Authorization: Bearer {ZETARIS_API_KEY}`, optional `X-Org-ID`, `X-Request-ID`. Need official auth scheme (API key vs OAuth, required org header, scopes). |
| **Request schema** | **MISSING.** Code may send `{"sql": "..."}` or `{"select","pageLimit"}` or `{"query","format"}`. Need official JSON body + required fields. |
| **Response schema** | **MISSING.** Code accepts multiple shapes (`headers`+`data`, `rows`, `results`, …). Need the canonical response for a successful query. |
| **Erros e retry policy** | **MISSING.** Code maps 401/403 → auth, 404 → not found, 5xx → retry (max 2). Need vendor error codes / idempotency / retry guidance. |
| **Limites de rate/tokens** | **MISSING.** No QPS, concurrency, or query-size limits documented locally. |
| **Como obter credencial de teste** | **MISSING.** No sandbox signup / trial key process in repo. |

### Currently assumed in code (UNVERIFIED — do not treat as contract)

- `ZETARIS_BASE_URL`, `ZETARIS_API_KEY`, `ZETARIS_ORG_ID`
- Path default: `/api/v1.0/sql-editor/sqls/run-query`
- Timeout default 30s; max attempts 2

### SAMPLE / local (does not need Zetaris contract)

- `SAMPLE_MODE=true` → `services/sample_data.py` only; **no network**. Tested offline.

### Blocked until

Official Zetaris REST (or Lightning SQL) guide answering every row above, plus one successful authenticated query against a test tenant.

---

## 2. Meterless Nightshift — Markovian engine

### What we need (official)

| Gap field | Status |
|-----------|--------|
| **URL base oficial** | **MISSING.** Code uses arbitrary `METERLESS_BASE_URL` (example `http://127.0.0.1:8088`). Need production/sandbox host from Meterless. |
| **Método e path** | **MISSING.** Code assumes `POST /api/v1/engines/markovian/run` (`METERLESS_MARKOVIAN_PATH`). **No official confirmation in repo.** |
| **Autenticação (header, scope)** | **MISSING.** Code sends `Authorization: Bearer {METERLESS_RUNTIME_TOKEN}`, `meterless-app`, `meterless-scope: user`, `X-Request-ID`. Need official header names, token type, and scopes. |
| **Request schema** | **MISSING.** Assumed body: `engine`, `plan`, `context`, `agents[]`, `config{max_revisions,target_score,timeout_per_step_seconds,model,...}`. Need vendor schema. |
| **Response schema** | **MISSING.** Code maps flexible shapes (`final_output` / `output` / `iterations` / `traces`). Need canonical response. |
| **Erros e retry policy** | **MISSING.** Local mapping: 401/403 auth, 404 not found, 5xx retry ≤2, 60s HTTP timeout. Need vendor policy. |
| **Limites de rate/tokens** | **MISSING.** No engine QPS, max agents, max revisions, or token budget from Meterless docs. |
| **Como obter credencial de teste** | **MISSING.** README mentions “Nightshift cockpit / proxy-token” without a documented procedure. |

### Currently assumed in code (UNVERIFIED — do not treat as contract)

See `adapters/meterless_adapter.py` and `.env.example`. **Do not call this “Meterless integrado”.**

### SAMPLE / local (does not need Nightshift contract)

- `SAMPLE_MODE=true` → in-process Planner→Writer⇄Evaluator (`provider=local_sample`). **Tested offline.** This proves our loop design, **not** Meterless product integration.

### Blocked until

Official Nightshift / Markovian API contract + one live `run` with a test runtime token returning a documented response shape.

---

## 3. Meterless H-MEM (remote long-term memory)

### What we need (official)

| Gap field | Status |
|-----------|--------|
| **URL base oficial** | **MISSING** |
| **Método e path** | **MISSING** — intentionally **not invented** |
| **Autenticação** | **MISSING** |
| **Request schema** | **MISSING** (project sync / restore payloads) |
| **Response schema** | **MISSING** |
| **Erros e retry policy** | **MISSING** |
| **Limites de rate/tokens** | **MISSING** |
| **Como obter credencial de teste** | **MISSING** |

### What exists today (honest)

- **Local persistence implemented** (`MemoryService` + Django models / in-memory backend).
- `HMemAdapter.sync_project` / remote path returns or raises **“H-MEM remote contract pending”**.
- Env `METERLESS_HMEM_ENABLED` must stay `false` until a contract exists.

### Blocked until

Official Meterless H-MEM API documentation. Until then: **no remote sync code that pretends to call H-MEM.**

---

## 4. NVIDIA NIM / Nemotron

### What we need (official, checked into or linked from this repo)

| Gap field | Status |
|-----------|--------|
| **URL base oficial** | **UNVERIFIED in-repo.** Code defaults to `https://integrate.api.nvidia.com/v1`. Need confirmation from NVIDIA NIM docs (and pinned version) stored or cited as verified. |
| **Método e path** | **UNVERIFIED.** Code uses OpenAI-compatible `GET /models`, `POST /chat/completions`. Need confirmation those are the supported NIM routes for our account/region. |
| **Autenticação** | **UNVERIFIED.** Assumed `Authorization: Bearer {NVIDIA_API_KEY}`. Need key issuance docs (NGC / build.nvidia.com / other). |
| **Request schema** | **UNVERIFIED.** Assumed chat-completions JSON (`model`, `messages`, `temperature`, `max_tokens`, `stream:false`). |
| **Response schema** | **UNVERIFIED.** Assumed `choices[0].message.content` + optional `usage`. |
| **Erros e retry policy** | **UNVERIFIED.** Local: 401/403 auth, 404 model, 5xx retry ≤2, 30s timeout. |
| **Limites de rate/tokens** | **MISSING** in repo (RPM, TPM, model-specific max tokens). |
| **Como obter credencial de teste** | **MISSING** in repo (step-by-step for hackathon / NGC key). |
| **Model id allow-list** | **UNVERIFIED.** Default `nvidia/nemotron-4-340b-instruct` and sample catalog are **not** proven available on the target NIM endpoint. |

### SAMPLE / local

- Heuristic `generate_text` + static `KNOWN_SAMPLE_MODELS` — **no network**. Not a NIM integration proof.

### Blocked until

Pinned official NIM documentation (or OpenAPI) for the exact base URL + model IDs, plus one authenticated `GET /models` and `POST /chat/completions` captured in an audit (no secrets in git).

---

## 5. Cross-cutting gaps

| Item | Status |
|------|--------|
| End-to-end live path Zetaris → Meterless → draft | **Never run** with real credentials |
| Meterless fail → NVIDIA direct fallback | **Code exists, unverified live** |
| Secret handling for live keys | Placeholders only in `.env.example`; **never commit real keys** |
| Judge demo without sponsors | Use `SAMPLE_MODE=true` only |

---

## 6. What to do when official docs arrive

1. Paste or link the vendor doc into `docs/hackathon/sponsors/` (or update this file with “VERIFIED” + doc URL + date).
2. Align adapter path/headers/schemas **exactly** to the doc (delete unused assumed modes).
3. Run a live smoke (credentials via env, not git) and attach redacted traces to `docs/hackathon/`.
4. Only then may README move a surface from `IMPLEMENTED_UNVERIFIED` → `IMPLEMENTED_REAL`.
5. **Never** mark Meterless as “integrado” without step 3 for the Markovian endpoint.

---

## 7. Local references searched (no official contract found)

- `HACKATHON_README.md`
- `backend/apps/reasoning_engine/README.md`
- `backend/apps/reasoning_engine/.env.example`
- `docs/hackathon/TEST_REPORT.md`
- `docs/**` (no Zetaris/Meterless/NVIDIA vendor kits)
- Adapter modules under `backend/apps/reasoning_engine/adapters/`
