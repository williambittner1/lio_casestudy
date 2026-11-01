# Lio Procurement App

Upload vendor-offer PDFs, extract the details with AI, and track procurement
requests across your organization.

- **`frontend/`** — Next.js 15, React 19, Tailwind CSS 4
- **`backend/`** — FastAPI, SQLAlchemy, Alembic, JWT auth
- **Database** — PostgreSQL 16

## Prerequisites

- **Docker Desktop** (or Docker Engine) with **Docker Compose v2** — running before you start.
- An **OpenAI API key** is optional: the app boots and runs without it; only PDF extraction needs one.

## Quick start (Docker)

```bash
cp .env.example .env      # optional: add your OpenAI API key for PDF extraction
docker compose up --build
```

| | URL |
| --- | --- |
| App | http://localhost:3200 |
| API + docs | http://localhost:7200 · http://localhost:7200/docs |

Sign in with a demo account — no registration needed (password `demo1234`).
Development happens in the **Acme Corp** organization, which has one of each role:

| Account | Role | Can do |
| --- | --- | --- |
| `admin@acme.com` | admin | everything incl. members & settings |
| `buyer@acme.com` | buyer | approve requests |
| `requester@acme.com` | requester | create & track requests |

For Acme, requests below €2,000 are approved automatically; requests at or
above €2,000 need a buyer or admin to approve them. Changes to request contents
or the attached document trigger a fresh approval check. An eligible smaller
request is automatically approved again; otherwise it returns to pending.
Status changes are separate workflow actions. Buyers and admins can approve
their own pending requests; rejection of one's own request remains restricted.
Other organizations retain manual approval for all amounts.
Existing requests are not retroactively changed by the migration.

Acme's categorization rules are visible to admins in **Organization → Settings**
and documented in [handover.md](handover.md). The €20 monthly subscription
threshold and its tax basis still need customer validation.

Other tenants exist to show the multi-tenant setup — **Walmart, Schaeffler,
Siemens, BMW** — each with an admin at `admin@<company>.com` (same password).
Their data is fully isolated from Acme's.

On startup the backend applies schema migrations and seeds the database (demo
accounts, organizations, commodity groups, suppliers) — both steps are
idempotent, so restarts never duplicate or overwrite anything (your data lives
in the `postgres_data` volume; `docker compose down -v` resets it). The OpenAI
key is only needed for PDF extraction — everything else works without it.
Keep the key in the ignored `.env` file; `.env.example` is a tracked template.

## Local development

```bash
docker compose up db        # Postgres only

# backend  → http://localhost:7200
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e .
alembic upgrade head        # schema
python -m app.seed          # reference data (idempotent)
uvicorn app.main:app --reload --port 7200

# frontend → http://localhost:3200
cd frontend
npm install
npm run dev
```

## Tests

```bash
# Backend: install project dependencies plus the test tools first.
cd backend
pip install -e . pytest httpx
OPENAI_API_KEY= python -m pytest tests/ -q   # in-memory SQLite; no model calls

# From the app root, test the frontend (Node 24 for the TypeScript test files).
cd ../frontend
node --test tests/*.test.mjs
npx tsc --noEmit
npm run build
```

See [handover.md](handover.md), [SOLUTION_NOTES.md](SOLUTION_NOTES.md),
and [pdf-benchmark.md](pdf-benchmark.md) for the implemented behavior,
evidence, limitations and remaining work.

## Layout

```
backend/    FastAPI app — routers, services, models, schemas, core; Alembic migrations
frontend/   Next.js app — app-router pages, typed API client (src/lib/api), components
docker-compose.yml
```
