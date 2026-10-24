> **Public submission export** — this repo is isolated from the private Klio monorepo.
> Use `SAMPLE_MODE=true` (see root `README.md` → *Run in 60 seconds*).
> Live sponsor adapters are **unverified**; see `backend/apps/reasoning_engine/CONTRACT_GAPS.md`.

# Klio Reasoning Engine — Open Agent Hackathon 2026 Submission

## Overview

| | |
|---|---|
| **Track** | Wildcard [Tinkerer] |
| **Project** | Klio — SaaS for Brazilian cultural producers / collectives |
| **Hackathon feature** | Agentic Grant Writer (Reasoning Engine) |
| **Branch** | `hackathon/reasoning-engine` |
| **App path** | `backend/apps/reasoning_engine/` |

Klio is an existing production SaaS that helps cultural producers find and apply for grants. For this hackathon we added an autonomous **Agentic Grant Writer**: Planner → Writer ⇄ Evaluator, orchestrated with Meterless Markovian bounds, fed by Zetaris grant context, with NVIDIA Nemotron as the reasoning backend (via Meterless + direct fallback).

## What we built (hackathon window)

1. **Fetch grant rules** via Zetaris (Lightning SQL over REST) — read-only  
2. **Plan** application structure (`PlannerAgent`)  
3. **Draft** sections (`WriterAgent`, NVIDIA via Meterless / direct fallback)  
4. **Evaluate / critique** (`EvaluatorAgent`, target score ≥ 8.0)  
5. **Iterate ≤ 3 revisions** (hard bound)  
6. **JSONL traces** of every handoff (`logs/trace-<run_id>.jsonl`)  
7. **Persistent project memory** — resume sessions days later with provenance + anti-hallucination  

### Session resume (why it matters)

Producers pause mid-proposal. Without memory, the agent either re-interrogates or invents
“confirmed” awards/partnerships. We store opaque `owner_key` + `grant_id` projects with
versioned drafts and facts that always carry **source + confidence + confirmed_by_user**.

> **local persistence implemented; H-MEM remote sync pending official API contract**  
> (`adapters/hmem_adapter.py` — no invented Meterless H-MEM URL)

Endpoints: `POST /draft/`, `GET /projects/<grant_id>/resume/`, `POST /projects/<project_id>/facts/`.

## Pre-existing components (declared — not scoring)

- Klio frontend (React + Vite)  
- Klio backend (Django 5 + DRF) outside `reasoning_engine`  
- Grant radar / CRM / billing (Asaas) / auth / WhatsApp digest  
- Oracle VM infrastructure  

## What’s new (hackathon work)

All load-bearing hackathon code lives under `backend/apps/reasoning_engine/` on branch `hackathon/reasoning-engine`:

| Piece | Path |
|-------|------|
| Zetaris adapter | `adapters/zetaris_adapter.py` |
| Meterless Markovian adapter | `adapters/meterless_adapter.py` |
| NVIDIA Nemotron adapter | `adapters/nvidia_adapter.py` |
| Agents | `agents/{planner,writer,evaluator}.py` |
| Orchestrator + tracer | `services/{orchestrator,tracer,sample_data}.py` |
| Project memory | `services/memory_service.py`, `models.py`, `adapters/hmem_adapter.py` |
| HTTP entry | `POST /api/reasoning_engine/draft/` + resume/facts endpoints |
| Golden-trace script | `scripts/generate_golden_trace.py` |

## Sponsor technology

### Zetaris (Unified Semantic Layer)

**Role (intended):** Single read path for grant rubric / eligibility / budget / deadlines. The Planner does **not** write to Klio’s production radar DB.

**Impl:** `adapters/zetaris_adapter.py`  
**SAMPLE_MODE:** synthetic context from `services/sample_data.py` (clearly labeled) — **tested**.  
**Live REST:** **IMPLEMENTED_UNVERIFIED** — SQL-editor path/auth/schema **not** confirmed by official docs in this repo (`CONTRACT_GAPS.md` §1).

Assumed read query shape (warehouse-side; **unverified** against a real Zetaris tenant):

```sql
SELECT grant_id, title, description, rubric_criteria,
       budget_limits, eligibility_rules, deadlines, source
FROM grants
WHERE grant_id = '<grant_id>'
LIMIT 1
```

### Meterless (Markovian orchestration)

**Role (intended):** Bounded agent loop — handoffs, max 3 revisions, target score 8.0.

**Impl:** `adapters/meterless_adapter.py`  
**SAMPLE_MODE:** local Markovian loop (`provider=local_sample`) — **tested**, no network.  
**Live Nightshift:** client code exists but is **IMPLEMENTED_UNVERIFIED** — **not “integrado”**. Assumed path/payload are **not** official docs. See `backend/apps/reasoning_engine/CONTRACT_GAPS.md`.

### NVIDIA Nemotron (reasoning)

**Intended primary path:** model id via Meterless `config.model` (blocked on Meterless contract).  
**Secondary client:** `NvidiaAdapter` — health / validate / `list_models` / direct fallback (`provider=nvidia_direct`) — **IMPLEMENTED_UNVERIFIED** (no live key test in repo).  
**SAMPLE:** heuristics only. Gaps: `CONTRACT_GAPS.md` §4.

### Cursor

All hackathon code for this feature was developed in Cursor (scaffold, adapters, tracer, docs).

## How to run (judges)

### Prerequisites

```bash
git clone <repo-url>
cd klio
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# Copy placeholders only — never commit real secrets
cp apps/reasoning_engine/.env.example .env   # or merge into your env
```

### SAMPLE_MODE (recommended — no credentials)

```bash
export SAMPLE_MODE=true
export REASONING_ENGINE_ENABLED=true
export DJANGO_SETTINGS_MODULE=config.settings
export DJANGO_SECRET_KEY=dev-only
export DEBUG=true
export DATABASE_URL=sqlite:////tmp/klio-hackathon.sqlite3

# Regenerate golden traces + I/O examples
python backend/apps/reasoning_engine/scripts/generate_golden_trace.py
# (run from repo root; script adds backend/ to sys.path)
```

Single run:

```bash
cd backend
python - <<'PY'
import django, os
os.environ.update(SAMPLE_MODE="true", REASONING_ENGINE_ENABLED="true",
                  DJANGO_SETTINGS_MODULE="config.settings")
django.setup()
from apps.reasoning_engine.services.orchestrator import ReasoningOrchestrator
r = ReasoningOrchestrator().run(
    grant_id="sample-grant-teatro-sp",
    user_portfolio_text="Coletivo de teatro com circulação e audiodescrição...",
    output_language="pt-BR",
)
print(r["status"], r["output"]["score"], r["log_file"])
PY
```

### Live APIs (optional)

```bash
export SAMPLE_MODE=false
export ZETARIS_BASE_URL=https://api.enterprise.zetaris.com
export ZETARIS_API_KEY=...
export ZETARIS_ORG_ID=...
export METERLESS_BASE_URL=http://127.0.0.1:8088
export METERLESS_RUNTIME_TOKEN=...
export NVIDIA_API_KEY=...
export NEMOTRON_MODEL=nvidia/nemotron-4-340b-instruct
```

See `backend/apps/reasoning_engine/README.md` and `.env.example` for full knobs.

### HTTP endpoint

```bash
cd backend
REASONING_ENGINE_ENABLED=true SAMPLE_MODE=true python manage.py runserver 0.0.0.0:8002

curl -s -X POST http://127.0.0.1:8002/api/reasoning_engine/draft/ \
  -H "Content-Type: application/json" \
  -d @../input_examples/reasoning_engine/example_1.json
```

## Sample inputs / outputs / golden traces

| # | Input | Output | Golden trace |
|---|-------|--------|--------------|
| 1 | `input_examples/reasoning_engine/example_1.json` (pt-BR) | `output_examples/reasoning_engine/example_1_output.json` | `logs/golden_trace_example_1.jsonl` |
| 2 | `example_2.json` (en) | `example_2_output.json` | `logs/golden_trace_example_2.jsonl` |
| 3 | `example_3.json` (es) | `example_3_output.json` | `logs/golden_trace_example_3.jsonl` |

Index: `input_examples/reasoning_engine/README.md`

### Trace format

Each line is one JSON event:

```json
{
  "timestamp": "2026-10-22T15:35:29.962875+00:00",
  "run_id": "...",
  "agent_name": "Planner",
  "action": "plan",
  "input_summary": "...",
  "output_summary": "...",
  "target_agent": "Writer",
  "confidence": 0.85,
  "retry_count": 0,
  "status": "success",
  "provider": "sample"
}
```

What judges should see:

- Handoffs Planner → Writer → Evaluator → Writer (revise) → Evaluator  
- Score path e.g. 6.5 → revision → ≥ 8.0  
- Bounded loop (never infinite)  
- `provider` = `sample` / `local_sample` in SAMPLE_MODE; `meterless` / `nvidia_direct` in live  

## Architecture (short)

```
Request (grant_id + portfolio + language)
   → ZetarisAdapter (rubric context)
   → Planner (seed outline)
   → MeterlessAdapter Markovian loop
        Writer ⇄ Evaluator (≤ 3 revisions, target 8.0)
        [fallback: NvidiaAdapter direct if Meterless fails]
   → Tracer JSONL + structured response
```

## Limitations (honest)

1. **SAMPLE_MODE uses heuristics**, not live LLM reasoning. It proves architecture, handoffs, bounds, and traces — not full Nemotron prose quality.  
2. **Sample grant rows are synthetic** and labeled `[SAMPLE_MODE]`. Production radar data is not injected into sample mode (D-008 / no fake radar writes).  
3. **No human-in-the-loop / Discord approval** for drafts yet.  
4. **No automatic portal submission** — the system drafts text; humans still file.  
5. **Live Zetaris/Meterless/NVIDIA** need **official** API contracts (see `CONTRACT_GAPS.md`) plus real credentials; current live clients are **unverified** and must not be called “integrated”.  
6. **H-MEM remote sync is not live** — local memory works; remote Meterless H-MEM waits on an official API contract (we refuse to invent endpoints).

## What works well

- Hard bounds (time + revisions)  
- Transparent JSONL traces  
- Graceful degradation: Meterless → NVIDIA direct → SAMPLE_MODE  
- Isolated Django app — production billing/CRM/radar untouched  
- Sponsor **slots** are load-bearing in the design; live HTTP remains **unverified** until official contracts + smoke tests (`CONTRACT_GAPS.md`)  

## Team / contact

- Builder: Kaique Theo  
- Contact: kaique@klio.app.br  
- Product: https://klio.app.br  

## Submission checklist

- [x] Zetaris adapter **code** (live = unverified; SAMPLE tested)  
- [x] Meterless-shaped **local** Markovian loop (SAMPLE tested) — live Nightshift **not** marked integrated  
- [x] NVIDIA adapter **code** (live = unverified; SAMPLE heuristics)  
- [x] Cursor development  
- [x] SAMPLE_MODE without credentials  
- [x] JSONL traces with real handoffs / critique / retry  
- [x] ≥ 3 sample inputs & outputs + golden traces  
- [x] Programmatic entry (CLI script + HTTP)  
- [x] No secrets committed  
- [x] Pre-existing components declared  
- [x] Limitations + `CONTRACT_GAPS.md` documented  
- [x] Local persistent memory + provenance / resume endpoints  
- [ ] Official Zetaris / Meterless / NVIDIA contracts filled + live smoke  
- [ ] Demo video (record next)  
- [ ] Slide deck (create next)  
