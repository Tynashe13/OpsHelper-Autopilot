# OpsHelper Autopilot — Procurement Exception Command Center

An AI system that catches supply chain/procurement exceptions, evaluates them against configurable policies, and routes decisions to a human whenever a policy requires it instead of auto-approving by default.

---

## Prerequisites

| Tool | macOS | Windows | Why you need it |
|------|-------|---------|-----------------|
| **Docker Desktop** | [Download for Mac](https://www.docker.com/products/docker-desktop/) | [Download for Windows](https://www.docker.com/products/docker-desktop/) | Runs all services (backend, frontend, database) in containers |
| **Git** | Pre-installed or `brew install git` | [Download](https://git-scm.com/download/win) or `winget install Git.Git` | Clone the repository |

> **Windows users:** Make sure WSL 2 is enabled (Docker Desktop will prompt you). If you see a WSL error, run `wsl --install` in PowerShell as Administrator and restart.

---

## Getting Started

### 1. Clone the repository
```bash
git clone https://github.com/Tynashe13/OpsHelper-Autopilot.git
cd OpsHelper-Autopilot
```

### 2. Create your environment file

**macOS / Linux:**
```bash
cp .env.example .env
```

**Windows (PowerShell):**
```powershell
Copy-Item .env.example .env
```

By default, `AUTH_BYPASS=true` skips authentication setup entirely — the app starts with a "Dev User" session automatically. Fill in one LLM provider key (`ANTHROPIC_API_KEY`, `GROQ_API_KEY`, or `GEMINI_API_KEY`, matching whichever you set `LLM_PROVIDER` to) if you want natural-language policy evaluation to work. `SUPABASE_DB_URL` is optional — leave it unset and the app runs normally; setting it enables automatic ingestion of new records from a Supabase table.

### 3. Start Docker Desktop
Open Docker Desktop and wait for the tray icon to show it's running before continuing.

### 4. Start all services
```bash
docker compose up --build
```

> First run takes several minutes to download images and install dependencies. Subsequent runs are much faster.
>
> **First-run note:** on a brand-new database volume, Postgres needs extra time to initialize, which can occasionally exceed the default healthcheck window and produce a one-time `dependency failed to start: postgres is unhealthy` error. Running the same command again resolves it — the slow step only happens once.

### 5. Verify everything is running
```bash
docker compose ps
```
You should see three services with status `running` or `Up`.

### 6. Open the app

| Service | URL | What it is |
|---------|-----|------------|
| **Dashboard** | http://localhost:3001 | Main web UI |
| **API Docs** | http://localhost:8001/api/docs | Backend Swagger documentation |
| **Database** | localhost:5432 | PostgreSQL (user: `user`, password: `password`) |

---

## Stopping & Restarting

| Action | Command |
|--------|---------|
| Stop everything | `docker compose down` |
| Restart without rebuilding | `docker compose up -d` |
| Full rebuild after code changes | `docker compose up --build -d` |
| Clean reset (wipes all data) | `docker compose down` then `docker volume rm opshelper-autopilot_postgres_data opshelper-autopilot_document_storage` then `docker compose up --build -d` |

macOS/Linux users can substitute `make up` / `make down` — run `make help` for the full list of shortcuts.

---

## What the system does

| Pillar | What it does |
|--------|---------------|
| **AI Policies** | Rules written in plain English or built structurally, evaluated by an LLM or a deterministic DSL engine depending on policy type |
| **Orchestrator** | Runs an incoming record through every active policy and decides whether to resolve it automatically or route it to a human |
| **Supabase ingestion** | Reads new rows from a configured Supabase table on an interval and feeds them through the same decision path automatically, no manual trigger required |
| **Workbench** | The human review queue. Items carry a retry/escalation clock; unresolved items get escalated to a different target after enough time passes |
| **Dashboard** | Live KPIs computed from real activity logs and workbench data |
| **AI Manager** | Chat/orchestration interface that triggers a live multi-agent workflow |
| **AI Insights** | Summarizes policy and workbench activity into plain-language findings |
| **Data Manager** | Registry and health-check API for connected external systems |

**Current limitations:**
- Authentication runs in dev-mode bypass by default (`AUTH_BYPASS=true`). A full JWT-based auth flow exists but requires configuring `KEYCLOAK_*` environment variables to enable.
- The Data Manager backend API is functional; there is currently no dedicated frontend page for it.
- Automated test coverage is minimal (a couple of smoke tests).

---

## Project Structure

OpsHelper-Autopilot/
├── app/
│   ├── main.py                    # App entry point, router registration, startup tasks
│   ├── security.py                # Auth logic (JWT validation, dev-mode bypass)
│   ├── authz.py                   # Authorization engine
│   ├── authz.map.json             # Per-route authorization rules
│   ├── models/                    # SQLAlchemy models
│   ├── schemas/                   # Pydantic request/response schemas
│   ├── routers/                   # API endpoints
│   │   ├── ai_policies.py
│   │   ├── orchestrator.py
│   │   ├── workbench.py
│   │   ├── dashboard.py
│   │   ├── data_manager.py
│   │   ├── ai_manager.py
│   │   ├── insights.py
│   │   ├── admin.py
│   │   ├── audit.py
│   │   └── auth.py
│   ├── services/
│   │   ├── policy_engine.py       # DSL + LLM policy evaluation
│   │   ├── llm_client.py          # Multi-provider LLM client (Anthropic / Groq / Gemini)
│   │   ├── orchestrator_engine.py # Core decision logic: auto-resolve vs. route to a human
│   │   ├── orchestrator_poller.py # Scheduled Supabase ingestion
│   │   ├── system_of_record.py    # Read-only Supabase table access
│   │   ├── triage.py              # Workbench item creation
│   │   ├── workbench_scheduler.py # Retry/escalation background job
│   │   ├── health_check.py        # Per-integration health checks
│   │   └── insights.py            # Insights generation
│   └── core/
│       ├── database.py            # Primary application database
│       ├── supabase_db.py         # Separate Supabase connection
│       └── storage.py
├── frontend/                      # Next.js application
├── alembic/
│   └── versions/                  # Database migrations
├── scripts/                       # seed_db.py, reset_db.py, cleanup_db.py
├── docs/                          # Architecture and design documentation
├── docker-compose.yml
├── Dockerfile
└── .env.example


---

## Key Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTH_BYPASS` | `true` | Skip authentication (dev mode). Set to `false` and configure `KEYCLOAK_*` variables for real auth |
| `DATABASE_URL` | auto-generated | Primary application database |
| `LLM_PROVIDER` | `anthropic` | LLM backend for policy evaluation and insights — `anthropic`, `groq`, or `gemini` |
| `ANTHROPIC_API_KEY` / `GROQ_API_KEY` / `GEMINI_API_KEY` | - | Only the key matching `LLM_PROVIDER` is required |
| `SUPABASE_DB_URL` | unset | Optional. If set, enables automatic ingestion from a Supabase table |
| `SUPABASE_SOR_TABLE` | `disruptions` | Which Supabase table is polled |
| `SUPABASE_SOR_ENTITY_NAME` | `recovery_plan` | Which policy `entity_name` incoming Supabase rows are evaluated against |
| `ORCHESTRATOR_POLL_INTERVAL_SECONDS` | `30` | How often the Supabase poller checks for new rows |
| `FRONTEND_URL` | `http://localhost:3001` | CORS origin |

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Docker not found / "Docker Desktop is unable to start" | Open the Docker Desktop application and wait for the tray icon to show it's running before running any `docker` command |
| `docker compose up` fails with `postgres is unhealthy` | First-run only. Run the same command again |
| `npm ci` / build fails with `ETIMEDOUT` | Network hiccup inside the container. Retry the build; if it persists, check for an active VPN or run `wsl --shutdown` (as Administrator) and relaunch Docker Desktop |
| Port 3001 already in use | Stop whatever is using it, or change the port mapping in `docker-compose.yml` |
| Port 5432 already in use | A local PostgreSQL instance may already be running — stop it or change the port |
| WSL error (Windows) | Run `wsl --install` in PowerShell as Admin, then restart |
| Containers crash-looping | Check logs: `docker compose logs backend` |
| Database connection refused | Wait 10-15 seconds after startup for Postgres to initialize |
| Supabase records aren't creating Workbench items | Confirm `SUPABASE_SOR_ENTITY_NAME` matches an active policy's `entity_name` exactly |

---

## Documentation

| Document | Purpose |
|----------|---------|
| [Command Center Guide](docs/command-center-guide.md) | Architecture overview |
| [Hackathon Brief](docs/hackathon-brief.md) | Problem statements, judging criteria |
| [Design System](docs/design-system-template.md) | UI component patterns, colors, spacing |
| [Audit System](docs/Audit%20System%20Guide.md) | Audit logging architecture |

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | Python 3.11 + FastAPI |
| Frontend | Next.js 15 + React 19 |
| Database | PostgreSQL 15 |
| ORM | SQLAlchemy 2 + Alembic |
| LLM | Anthropic / Groq / Gemini (configurable) |
| Scheduling | APScheduler |
| Auth | JWT (Keycloak-compatible, bypassable for dev) |
| UI | Tailwind CSS + Framer Motion |
| Containers | Docker + Docker Compose |