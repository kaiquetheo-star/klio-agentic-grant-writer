# Intentionally excluded from this public submission

This tree is a **minimal hackathon export**. The private Klio monorepo was **not** made public.

## Excluded on purpose

| Path / area | Why |
|-------------|-----|
| Entire Klio monorepo root (frontend, private docs beyond hackathon) | Proprietary product |
| `infra/` | Production Oracle/Caddy/Compose, secrets wiring |
| `backend/.env`, `infra/.env.production`, any real `.env*` | Credentials |
| `backend/apps/accounts/`, `billing/`, `growth/`, `users/` | Auth / billing / CRM growth |
| `backend/apps/operations/` | Radar, WhatsApp, scraping, CRM |
| Production / demo Postgres data, backups, dumps | Real or seeded product data |
| `node_modules/`, frontend `dist/`, `.venv/` | Bulk / non-reproducible |
| `tmp/`, caches, `__pycache__/` | Ephemeral |
| Real API keys (Zetaris / Meterless / NVIDIA / Asaas / …) | Secrets |
| Private agent stores, vault mirrors, Syncthing paths | Out of scope |

## Included

Only `backend/apps/reasoning_engine/` (+ minimal Django shell), sample I/O, golden traces, hackathon docs, and placeholders in `.env.example`.
