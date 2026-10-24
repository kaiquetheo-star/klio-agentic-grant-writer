# Klio Reasoning Engine — Open Agent Hackathon 2026 (public submission)

Isolated **Agentic Grant Writer**: Planner → Writer ⇄ Evaluator (≤ 3 revisions),  
JSONL traces, SAMPLE_MODE offline demo, and **local** project memory with provenance.

This repository is a **minimal export** for judges. It does **not** include Klio’s
production SaaS (billing, CRM, auth, WhatsApp, scraping, or private infra).

> Live Zetaris / Meterless / NVIDIA clients are **IMPLEMENTED_UNVERIFIED**.  
> Meterless is **not** marked “integrado”. See `backend/apps/reasoning_engine/CONTRACT_GAPS.md`.

## Run in 60 seconds

```bash
git clone <repo-publico>
cd klio-reasoning-engine-submission

python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt

export SAMPLE_MODE=true
export REASONING_ENGINE_ENABLED=true
export REASONING_ENGINE_MEMORY_BACKEND=memory
export DJANGO_SECRET_KEY=submission-dev-only
export DEBUG=true
export ALLOWED_HOSTS=localhost,127.0.0.1,0.0.0.0

cd backend
python manage.py migrate --run-syncdb
SAMPLE_MODE=true python manage.py runserver 0.0.0.0:8002
```

In another terminal:

```bash
curl -s -X POST http://127.0.0.1:8002/api/reasoning_engine/draft/ \
  -H 'Content-Type: application/json' \
  -d @../input_examples/reasoning_engine/example_1.json | head
```

### Docker

```bash
docker build -t klio-reasoning-engine .
docker run --rm -e SAMPLE_MODE=true -p 8002:8002 klio-reasoning-engine
```

## Tests (offline, no credentials)

```bash
cd backend
export SAMPLE_MODE=true REASONING_ENGINE_ENABLED=true REASONING_ENGINE_MEMORY_BACKEND=memory
export DJANGO_SECRET_KEY=submission-dev-only DEBUG=true
python manage.py test apps.reasoning_engine --verbosity 2
```

## What’s inside

| Path | Purpose |
|------|---------|
| `backend/apps/reasoning_engine/` | Full hackathon app (adapters, agents, memory, tests) |
| `backend/config/` | Minimal Django + DRF settings/urls |
| `input_examples/` / `output_examples/` | Sample I/O |
| `logs/golden_trace_*.jsonl` | Golden JSONL handoff traces |
| `docs/hackathon/TEST_REPORT.md` | Audit report (SAMPLE_MODE) |
| `HACKATHON_README.md` | Submission narrative |

## Intentionally excluded

Private Klio monorepo material: `infra/`, production `.env*`, billing, accounts, growth,
operations/CRM, WhatsApp, scraping pipelines, Postgres data, backups, `node_modules/`,
`.venv/`, real user/edital data, and any real API keys.

## License / contact

Hackathon demo code for judging. Product: https://klio.app.br · kaique@klio.app.br
