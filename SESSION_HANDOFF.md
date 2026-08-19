# OpsHelper Autopilot — Session Handoff

Covers everything done in this conversation, in order, so a fresh session
(human or AI) can pick up without re-deriving any of it. Written after
discovering the repo has forked into two divergent implementations —
see §6 — which is why this exists now rather than at the end.

---

## 1. Starting point

Picked up a prior session's work via an uploaded chat transcript + code
files. That session had built a full Workbench system (models, routers,
scheduler, triage) locally and verified it end-to-end, but **the handoff
doc's claim that it was pushed to GitHub was false** — checking the real
repo showed none of it existed on `origin/main`. This "committed ≠ wired
in / committed ≠ pushed" gap turned out to be a recurring theme all
session (see §3, §6).

## 2. First build pass (before the repo moved)

Rebuilt the missing Workbench system from scratch against the real repo
(`https://github.com/Tynashe13/OpsHelper-Autopilot`), matching existing
conventions (`models/policy.py`, `routers/ai_policies.py`,
`services/policy_engine.py`):

- `app/models/workbench.py` — `WorkbenchItem`, `WorkbenchStatus`,
  `WorkbenchSeverity`
- `app/schemas/workbench.py`
- `app/services/notifications.py` — stubbed Slack boundary
- `app/services/triage.py` — deterministic auto_resolve /
  route_to_workbench / escalate decision, reading Policy Engine verdicts
- `app/services/workbench_scheduler.py` — APScheduler retry/escalation
  ladder
- `app/routers/workbench.py`, `app/routers/orchestrator.py`
- Alembic migration for `workbench_items`, chained onto the real head
- Fixed `app/models/__init__.py` — `Policy`/`PolicyEvaluation`/
  `DataSource`/`WorkbenchItem` existed as files but weren't imported, so
  `Base.metadata.create_all()` silently skipped their tables

Verified via `TestClient` + real E2E run (create policy → orchestrator
event → triage → workbench → scheduler retry×2 → escalate → resolve →
re-resolve correctly rejected as 409) against SQLite. **Never pushed** —
explicit user instruction was local-only until they reviewed it.

## 3. First independent commits appeared on `origin/main`

User pushed their own work (or another session's), independently of
everything in §2 — different code, same architecture family:

- `2fca731 "Create Workbench"` — its own Workbench system. Verified live:
  **`models/__init__.py` was broken again** (same bug, regressed) —
  `Base.metadata.create_all()` only created `audit_logs`/`items`/
  `settings`. No orchestrator/live-trigger existed at all — only a
  manual `POST /api/workbench` create endpoint.
- Also found: `frontend/src/app/ai/policies/page.tsx` was 100%
  hardcoded `DEMO_POLICIES`, never called the real API at all — this is
  why the Policies page showed unrelated demo data regardless of what
  was actually in the database.

User chose: patch their pushed version rather than replace it with §2's
build.

## 4. Patches applied to `origin/main`'s version

- Fixed `models/__init__.py` — confirmed via `create_all()` all 7 tables
  now register.
- Added `app/routers/orchestrator.py` + `_decide()` logic (auto_resolve /
  route_to_workbench / escalate based on matched policy action types),
  wired into `main.py`/`routers/__init__.py`/`authz.map.json`.
- Wired `ai/policies/page.tsx` to the real API (`GET`/`PATCH`/`DELETE`/
  `POST /api/ai/policies`), removed `DEMO_POLICIES` entirely, added an
  error banner.
- Verified full E2E again against this patched version specifically —
  same pipeline, same result.
- `npx tsc --noEmit` clean on the frontend changes.
- Recorded a real Supervity Auto workflow ID
  (`019f7b51-f797-7000-8761-d325557229fa`) into a local `.env`
  (gitignored) for future reference — **not currently used**, since
  `policy_engine.py` uses direct-LLM calls (`llm_client.py`), not Auto
  (Auto's `workflow-runs/execute` endpoint is fire-and-forget, not
  synchronous — established earlier in the project's own history).

None of this was pushed — kept local per instruction, ready to compare
against whatever landed next.

## 5. Repo checked again — two more independent commits had landed

- `a278d6d` "Add Orchestrator ingest endpoint, fix models/__init__.py
  table registration" + `adbacb8` "Wire frontend Policies page to real
  API; rebuild Workbench page with live data + Simulate Disruption
  trigger" — **an independently-written implementation of the exact
  same two fixes from §4**, different code, same bugs found and fixed.
  Verified live: all 7 tables register, orchestrator endpoint works,
  full retry→escalate→resolve chain works, Workbench frontend page
  rebuilt (not just Policies) with a "Simulate Disruption" trigger.
  `tsc --noEmit` clean.

Confirmed via direct grep that `env.example` (no dot) and `.env.example`
had drifted apart (one had the Anthropic section, the other the Auto
section, neither had both) — flagged, not fixed (low priority).

Gave a build rating (70/100) and a checklist of what was still missing:
AI Insights (frontend mock + **no backend endpoint at all**), Data
Manager (backend works, **zero frontend**), real auth (Keycloak fully
implemented in `security.py` but dormant behind `AUTH_BYPASS=true`), and
almost no automated test coverage (`tests/test_main.py` was 34 lines,
two smoke tests).

Confirmed Supabase was **not connected** — only an `unconfigured` seed
`DataSource` row referencing it as an example.

## 6. Round 3 plan written (`ROUND3_PLAN.md`, delivered as a file)

Five-part plan, in dependency order: Supabase system-of-record
connection (direct Postgres, not REST — simpler, matches existing
patterns) with an **Orchestrator poller** (the actual mechanism that
makes it "read from a database" instead of "wait for a manual POST") →
AI Insights (backend + frontend, doesn't exist yet) → Data Manager
frontend (doesn't exist yet) → real auth (Keycloak, already built,
just needs configuring) → automated tests. Docker/local-run
verification explicitly excluded — user is handling that themselves.

User then asked for Anthropic + Groq + Gemini support specifically (not
just Anthropic) for the LLM judgment path — built a
multi-provider `services/llm_client.py` (`LLM_PROVIDER` env var selects
one of three, each via its own API key/model env vars; Groq and Gemini
called via plain `httpx` REST rather than adding SDK dependencies).
This was **left unfinished/unverified** — stopped mid-task to re-check
the repo per user request.

## 7. Supabase integration — built and verified against a stand-in

Built per the plan doc, all against the *then-current* `origin/main`
(`adbacb8`):

- `app/core/supabase_db.py` — second, fully separate SQLAlchemy engine/
  session, lazily initialized (missing `SUPABASE_DB_URL` doesn't crash
  app startup — the poller just doesn't start, logged not raised).
- `app/services/system_of_record.py` — `fetch_new_records(since)` /
  `latest_updated_at(rows)`, read-only, table name from
  `SUPABASE_SOR_TABLE` (validated against an identifier regex before
  being interpolated into SQL — env-sourced, but still checked).
- `app/services/orchestrator_engine.py` — **the previously-inline
  decision logic from `routers/orchestrator.py`, extracted into a shared
  service function (`process_event()`)** so the manual endpoint and the
  new poller are guaranteed to make identical decisions for identical
  input, by construction rather than by keeping two copies in sync.
  `routers/orchestrator.py` became a thin wrapper around it.
- `app/services/health_check.py` — added `_check_supabase()` (a real
  `SELECT 1`, not just an HTTP ping, run off the event loop via
  `anyio.to_thread`).
- `app/services/orchestrator_poller.py` — `AsyncIOScheduler` job,
  interval from `ORCHESTRATOR_POLL_INTERVAL_SECONDS` (default 30s),
  reads the `DataSource` row named "Orders & Inventory Store" for its
  `last_polled_at` high-water mark, fetches anything newer, runs each
  row through `process_event()`, advances the mark to the batch's own
  max `updated_at` (not `datetime.now()` — avoids clock-skew bugs).
- New column `DataSource.last_polled_at` + migration
  (`a2b3c4d5e6f7`), chained onto the real head at the time
  (`f6g7h8i9j0k1`).
- `main.py` — poller started/stopped in the existing
  `@app.on_event("startup"/"shutdown")` handlers, alongside the
  Workbench scheduler.

**Verified, including a real bug caught by testing, not just written:**
stood up a fake "Supabase" as a separate SQLite DB with a `disruptions`
table (since the real `supabase.co` isn't reachable from this sandbox's
network allowlist), seeded rows via SQLAlchemy directly (not raw
`sqlite3`, see below), and ran `_poll_once()` for real. First run
duplicated every record on the second, no-op poll — root cause was
`latest_updated_at()` assuming `updated_at` values were always real
`datetime` objects; SQLite returns strings, so the cursor never actually
advanced (masked further by a *second*, separate bug: my first seeding
script used raw `sqlite3` with mismatched timestamp formatting —
`isoformat()` in the seed data vs. Python's default `str(datetime)`
adapter for the query parameter — which silently broke the `WHERE
updated_at > :since` filter itself). **Fixed both**: `latest_updated_at`
now parses string timestamps defensively, and the test harness was
redone seeding through the same SQLAlchemy parameter-binding path the
real query uses, so the comparison is apples-to-apples. Confirmed
correct after the fix: first poll picks up all 3 seeded rows and creates
3 Workbench items with correctly-inferred priority; second poll (no new
data) creates zero; inserting one new row and polling again picks up
exactly that one row, not the earlier three again.

**This work was built against `adbacb8`, before `7e1a7cd`/`28af97d`
landed on `origin/main` — see §8, it has NOT been reconciled with what's
now actually on the branch.** Not pushed.

## 8. Repo checked again — more independent commits, plus a wildcard

- `7e1a7cd "Add Supabase system-of-record poller..."` — **its own commit
  message states it continues "a teammate's in-progress work... done in
  a separate local sandbox, verified via transcript but never
  committed"** — i.e., it explicitly picked up from a summary of this
  session's §7 work and rebuilt the pieces not directly handed over.
- `28af97d "Reconfigure Database"` — restructured several of those same
  files further. **Not yet deep-verified** — deprioritized once the zip
  (below) turned out to be the bigger issue.

## 9. Uploaded zip analyzed — a completely separate implementation

User uploaded `OpsHelper-Autopilot-merged.zip`. Diffed against
`origin/main`: **no meaningful git relationship** — even baseline
template files (`.gitignore`, `README`, `Dockerfile`, `Makefile`) differ
completely. This is a parallel, independently-built architecture:

| | `origin/main` | The zip |
|---|---|---|
| Core entity | `WorkbenchItem` + `Policy`/`PolicyEvaluation` + separate Orchestrator | `DisruptionRun` (decision baked into one entity) |
| Trigger endpoint | `POST /api/orchestrator/events` + Supabase poller | `POST /api/disruptions` |
| AI Insights | Doesn't exist | Real: `ai_insights.py` + `insight_engine.py` |
| Dashboard | Doesn't exist | Real: `dashboard.py` |
| Supabase access | Direct Postgres (`supabase_db.py`) | REST API via `httpx` (`supabase_client.py`) — a different, also-valid approach |
| Workbench backend | Built, verified | Doesn't exist — only a frontend page remains (calls `/api/disruptions` instead, at least internally consistent) |

**Immediate security flag**: the uploaded zip's `.env` contains a real,
non-empty `SUPABASE_SERVICE_KEY` (bypasses RLS entirely) and
`SUPABASE_URL`. Flagged to the user to rotate the key in Supabase's
dashboard — value was never displayed or logged anywhere.

**Three real bugs found by actually running it** (venv + `py_compile` +
`TestClient`), not just reading the code:

1. `services/llm_client.py` imports `anthropic`, which is missing from
   `packages/requirements.txt` entirely.
2. `routers/disruptions.py` imports `trigger_ops_helper`/
   `AutoOperatorError` from `services/auto_client.py` — **that file does
   not exist anywhere in the zip.** This breaks `POST /api/disruptions`,
   the one endpoint this entire architecture is built around — not an
   edge case, the app cannot serve its core function at all as uploaded.
3. The recurring bug, a third time, in a totally independent codebase:
   `Policy`/`PolicyEvaluation`/`DataSource` exist as files but aren't
   imported in `models/__init__.py`. Confirmed via `create_all()` —
   `policies`/`policy_evaluations`/`data_sources` tables silently never
   get created.

## 10. Decision point (current)

Presented four options for reconciling `origin/main` vs. the zip. User
chose: **merge — keep Workbench/Policy Engine/Orchestrator/Triage from
`origin/main` (the verified, working pillar), pull in Insights/
Dashboard/`supabase_client.py`'s REST approach from the zip, fix the
zip's 3 startup bugs and the recurring `models/__init__.py` bug along
the way, and get the merged result running smoothly.** This document is
the handoff for that merge — see the running conversation for the actual
merge work, which starts immediately after this file.

---

## Standing conventions worth knowing before touching more code

- **`models/__init__.py` needs its own recurring check** — this bug has
  now appeared 3 separate times across 2 independent codebases. Any
  merge work should treat "does `Base.metadata.create_all()` actually
  produce every expected table" as a mandatory verification step, not
  an assumption.
- **"Committed" is not "pushed", and "written" is not "verified"** — the
  single biggest recurring theme all session. Every claim in this
  document above was checked by actually running code (`TestClient`,
  `py_compile`, `create_all()`, `tsc --noEmit`, a real E2E script),
  never by reading source and assuming it works.
- **This sandbox cannot reach `supabase.co`** (not in the network
  allowlist) — any Supabase-touching work here is verified against a
  local stand-in (SQLite or a second Postgres), never the real service.
  Final confirmation against a real Supabase project has to happen on
  the user's machine.
- **Secrets discipline**: `.env` is gitignored in both lineages: real
  credentials go there, never into `.env.example`/`env.example`. Those
  two example files have drifted apart on `origin/main` and should
  probably be consolidated into one at some point.
