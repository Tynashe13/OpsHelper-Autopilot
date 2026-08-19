# 🚀 OpsHelper Autopilot — Procurement Exception Command Center

An AI Employee that catches supply chain/procurement exceptions, evaluates them against real policies, and knows when to hand a decision to a human instead of guessing. Built for the AutoPilot Hackathon — this repo has moved past the starter template into a working system with real backend logic behind every pillar below.

---

## Prerequisites

Before you begin, make sure you have these installed on your machine:

| Tool | macOS | Windows | Why you need it |
|------|-------|---------|-----------------|
| **Docker Desktop** | [Download for Mac](https://www.docker.com/products/docker-desktop/) | [Download for Windows](https://www.docker.com/products/docker-desktop/) | Runs all services (backend, frontend, database) in containers |
| **Git** | Pre-installed or `brew install git` | [Download](https://git-scm.com/download/win) or `winget install Git.Git` | Clone the repository |

> **Windows users:** Make sure WSL 2 is enabled (Docker Desktop will prompt you). If you see a WSL error, run `wsl --install` in PowerShell as Administrator and restart.

---

## 🚀 Getting Started — Step by Step

### Step 1: Clone the Repository
```bash
git clone https://github.com/Tynashe13/OpsHelper-Autopilot.git
cd OpsHelper-Autopilot
```

### Step 2: Create Your Environment File

**macOS / Linux:**
```bash
cp .env.example .env
```

**Windows (PowerShell):**
```powershell
Copy-Item .env.example .env
```

Then fill in at least one LLM provider key (`ANTHROPIC_API_KEY`, `GROQ_API_KEY`, or `GEMINI_API_KEY` — pick one and set `LLM_PROVIDER` to match) if you want natural-language policy evaluation to actually work. Everything else runs fine without it — `AUTH_BYPASS=true` means no external auth setup is needed either; the app starts with a "Dev User" session automatically.

Supabase integration (`SUPABASE_DB_URL`) is optional — leave it unset and the app runs normally, it just means the automatic ingest poller (see below) stays dormant until you configure it.

### Step 3: Start Docker Desktop
1. Open **Docker Desktop** from your Applications (Mac) or Start Menu (Windows)
2. Wait until the Docker icon in your system tray/menu bar shows **"Docker Desktop is running"**
3. If this is your first time, Docker may take 1-2 minutes to initialize

### Step 4: Start All Services

**macOS / Linux (Terminal):**
```bash
make up
```

**Windows (PowerShell):**
```powershell
docker compose up --build
```

> First run takes several minutes to download images and install dependencies. Subsequent runs are much faster thanks to layer caching.
>
> **First-run note:** on a brand-new database volume, Postgres has to initialize its entire cluster from scratch, which can take longer than the default healthcheck window and cause a one-time `dependency failed to start: container ... is unhealthy` error. If you hit this, just run the same command again — the slow initialization only happens once, and the retry starts in seconds.

### Step 5: Verify Everything is Running
```bash
docker compose ps
```
You should see 3 services with status `running` or `Up`.

### Step 6: Open Your Command Center

| Service | URL | What it is |
|---------|-----|------------|
| 🖥️ **Dashboard** | [http://localhost:3001](http://localhost:3001) | Live KPIs — real counts, not placeholders |
| ⚙️ **API Docs** | [http://localhost:8001/api/docs](http://localhost:8001/api/docs) | Backend Swagger documentation |
| 🗄️ **Database** | `localhost:5432` | PostgreSQL (user: `user`, password: `password`) |

---

## 🛑 Stopping & Restarting

| Action | Command |
|--------|---------|
| Stop everything | `docker compose down` |
| Restart without rebuilding | `docker compose up -d` |
| Full rebuild after code changes | `docker compose up --build -d` |
| Clean reset (wipes all data) | `docker compose down` then `docker volume rm opshelper-autopilot_postgres_data opshelper-autopilot_document_storage` then `docker compose up --build -d` |

macOS/Linux users can substitute `make up` / `make down` for the equivalent Makefile targets — run `make help` for the full list.

---

## What's actually built and working

Every pillar below has been run end-to-end against real data (created a policy, fired a real event, confirmed it landed in the right place with the right values) — not just written and assumed to work.

| Pillar | Status | What it does |
|--------|--------|---------------|
| **AI Policies** | Real | Write a rule in plain English or build one structurally; evaluated by an LLM (`app/services/policy_engine.py` + `app/services/llm_client.py`, supports Anthropic/Groq/Gemini) or by a deterministic DSL engine, depending on policy type |
| **Orchestrator** | Real | `POST /api/orchestrator/events` (or the frontend's "Simulate Disruption" button) runs an incoming record through every active policy and decides auto-resolve vs. route-to-human (`app/services/orchestrator_engine.py`) |
| **Supabase poller** | Real, verified against a stand-in DB | Reads new rows from a configured Supabase table every 30s and feeds them through the same Orchestrator decision path automatically — no manual trigger needed once `SUPABASE_DB_URL` is set (`app/services/orchestrator_poller.py`) |
| **Workbench** | Real | The human review queue. Items get a retry/escalation clock; a background scheduler bumps urgency and eventually escalates to a different target if nobody responds (`app/services/workbench_scheduler.py`) |
| **Dashboard** | Real | Live KPIs computed from actual `AuditLog`/`WorkbenchItem` data — every orchestrator decision (auto-resolved, routed, or failed) is logged, which is what makes real counts possible (`app/routers/dashboard.py`) |
| **AI Manager** | Built independently in this repo's history — triggers the live Auto orchestrator + 5 Operators on `auto.supervity.ai` | Not personally re-verified as part of this README update; see `app/routers/ai_manager.py` |
| **AI Insights** | Built independently in this repo's history — summarizes real Policy Evaluation/Workbench data via an LLM, with a 2-minute cache | Not personally re-verified as part of this README update; see `app/services/insights.py` |
| **Data Manager** | Backend only | The registry/health-check API for connected systems (`app/routers/data_manager.py`) works; there's currently no frontend page for it |
| **Real auth (Keycloak)** | Built, not turned on | `app/security.py` has a complete JWT/JWKS validation flow; `AUTH_BYPASS=true` bypasses it for easy local testing. Flip it off and configure `KEYCLOAK_*` env vars to use it for real |
| **Automated test suite** | Minimal | `tests/test_main.py` covers two smoke tests. Everything above was verified with manual scripts during development, not a committed, repeatable suite — this is the biggest honest gap if you're extending this further |

---

## Project Structure

OpsHelper-Autopilot/
├── app/
│ ├── main.py # App entry point, router registration, lifespan (scheduler + poller startup)
│ ├── security.py # Auth + AUTH_BYPASS logic (real Keycloak JWT validation, currently bypassed)
│ ├── authz.py # Authorization engine
│ ├── authz.map.json # Per-route authorization rules
│ ├── models/ # SQLAlchemy models (policy, workbench, data_source, audit, insight, ...)
│ ├── schemas/ # Pydantic request/response schemas
│ ├── routers/ # API endpoints — ai_policies, orchestrator, workbench, dashboard,
│ │ # data_manager, ai_manager, insights, admin, audit, auth
│ ├── services/
│ │ ├── policy_engine.py # DSL + LLM policy evaluation
│ │ ├── llm_client.py # Multi-provider LLM client (Anthropic/Groq/Gemini)
│ │ ├── orchestrator_engine.py # THE decision function — auto-resolve vs. route to a human
│ │ ├── orchestrator_poller.py # Scheduled Supabase ingest
│ │ ├── system_of_record.py # Read-only Supabase table access
│ │ ├── triage.py # Creates real Workbench items
│ │ ├── workbench_scheduler.py # Retry/escalation background clock
│ │ ├── health_check.py # Per-integration health checks for Data Manager
│ │ └── insights.py # AI Insights generation
│ └── core/ # database.py (primary DB), supabase_db.py (separate Supabase connection), storage.py
├── frontend/ # Next.js app — Dashboard, AI Policies, Workbench, AI Insights, AI Manager, Admin
├── alembic/versions/ # Database migrations, chained as a single history
├── scripts/ # seed_db.py, reset_db.py, cleanup_db.py
├── docs/ # Original hackathon docs (command-center-guide, hackathon-brief, etc.)
├── SESSION_HANDOFF.md # Detailed record of the Round 3 merge — what was built, what bugs were found and fixed, and how each was verified
├── docker-compose.yml
├── Dockerfile / frontend/Dockerfile
└── .env.example


---

## Key Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTH_BYPASS` | `true` | Skip all auth (dev mode) — set `false` and configure `KEYCLOAK_*` vars to use real auth |
| `DATABASE_URL` | auto-generated | This app's own primary database (Postgres via Docker) |
| `LLM_PROVIDER` | `anthropic` | Which LLM backs natural-language policy evaluation and Insights — `anthropic`, `groq`, or `gemini` |
| `ANTHROPIC_API_KEY` / `GROQ_API_KEY` / `GEMINI_API_KEY` | — | Only the one matching `LLM_PROVIDER` is required |
| `SUPABASE_DB_URL` | unset | Optional. If set, the Orchestrator poller starts automatically and reads new records every `ORCHESTRATOR_POLL_INTERVAL_SECONDS` |
| `SUPABASE_SOR_TABLE` | `disruptions` | Which Supabase table the poller reads from |
| `SUPABASE_SOR_ENTITY_NAME` | `recovery_plan` | Which Policy Engine `entity_name` incoming Supabase rows are evaluated as — must match your policies' `entity_name` or nothing will match |
| `ORCHESTRATOR_POLL_INTERVAL_SECONDS` | `30` | How often the Supabase poller checks for new rows |
| `FRONTEND_URL` | `http://localhost:3001` | CORS origin |

---

## 🛠️ Troubleshooting

| Problem | Solution |
|---------|----------|
| **Docker not found / "Docker Desktop is unable to start"** | Docker Desktop's own app isn't running — open it from the Start Menu/Applications and wait for the tray icon to go solid before running any `docker` command |
| **`docker compose up` fails with `dependency failed to start: postgres is unhealthy`** | First-run-only — Postgres's initial database setup can take longer than the healthcheck window. Just run the same command again; the slow step doesn't repeat |
| **`npm ci` / build fails with `ETIMEDOUT`** | Container network hiccup, not a code issue — retry the build. If it persists, check for an active VPN or try `wsl --shutdown` (as Administrator) then relaunch Docker Desktop |
| **Port 3001 already in use** | Stop whatever is on that port, or change the port mapping in `docker-compose.yml` |
| **Port 5432 already in use** | You have a local PostgreSQL running — stop it or change the port in `docker-compose.yml` |
| **WSL error (Windows)** | Run `wsl --install` in PowerShell as Admin, then restart your PC |
| **Containers crash-looping** | Check logs: `docker compose logs backend` — usually a missing env var or DB issue |
| **Database connection refused** | Wait 10-15 seconds after startup — Postgres needs time to initialize on first run |
| **Supabase-sourced records aren't creating Workbench items** | Check that `SUPABASE_SOR_ENTITY_NAME` matches an active policy's `entity_name` exactly — a mismatch means every record silently matches zero policies |

---

## Documentation

| Document | Purpose |
|----------|---------|
| **[Session Handoff](SESSION_HANDOFF.md)** | Detailed record of the Round 3 merge — every bug found, how it was verified, and what's still open |
| **[Command Center Guide](docs/command-center-guide.md)** | Original architecture guide from the hackathon template |
| **[Hackathon Brief](docs/hackathon-brief.md)** | Problem statements, judging criteria |
| **[Design System](docs/design-system-template.md)** | UI component patterns, colors, spacing |
| **[Audit System](docs/Audit%20System%20Guide.md)** | Audit logging architecture — also what backs the Dashboard's live KPIs |

---

## Tech Stack

| Layer | Technology | Purpose |
|-------|------------|---------|
| **Backend** | Python 3.11 + FastAPI | API server |
| **Frontend** | Next.js 15 + React 19 | Web dashboard |
| **Database** | PostgreSQL 15 | Persistent storage (this app's own tables; Supabase, if configured, is a separate read-only connection) |
| **ORM** | SQLAlchemy 2 + Alembic | Data modeling + migrations |
| **LLM** | Anthropic / Groq / Gemini (pick one via `LLM_PROVIDER`) | Natural-language policy evaluation, Insights generation |
| **Scheduling** | APScheduler | Workbench retry/escalation clock, Supabase ingest poller |
| **Auth** | Keycloak JWT (bypassable for dev) | Authentication |
| **UI** | Tailwind CSS + Framer Motion | Styling + animations |
| **Containers** | Docker + Docker Compose | Development environment |
