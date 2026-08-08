# OpsHelper Autopilot — Status Report & Next Steps

**Written:** August 9, 2026 · **Base:** `origin/main` zip as of Aug 8
(`PROJECT_STATUS_AND_NEXT_STEPS.md`'s confirmed HEAD `334a92a`), plus this
session's changes on top — **not yet pushed to `origin/main`, this is a
local zip only.** First thing the next session should do: diff this
against whatever's actually on `origin/main` by then, the same way this
doc's previous version told you to.

This doc replaces the Aug 8 version. Same discipline as before: every
claim below was verified by actually running something (`TestClient`,
`py_compile`, a real request through the app) — not by reading code and
assuming.

---

## 1. What changed this session

Starting point: the `origin/main`-based zip (`OpsHelper-Autopilot-main.zip`),
plus a set of loose files from a parallel session that had built the AI
Manager backend but never uploaded the actual `auto_client.py` rewrite,
the Insights backend, or a working (non-demo) Insights frontend page —
only the router/schema/wiring shells. This session:

1. **Applied the AI Manager wiring** that was actually uploaded and
   verified correct against the base: `app/routers/ai_manager.py`,
   `app/schemas/ai_manager.py`, `app/routers/__init__.py`, `app/main.py`,
   `app/authz.map.json` (adds `/api/ai-manager.*`, approved-user only).
2. **Wrote `trigger_orchestrator_run()`** in `app/services/auto_client.py`
   — this was referenced by the router but never actually included in
   anything uploaded. Execute-or-poll against Auto's
   `POST /workflow-runs/execute` (same auth/multipart contract as the
   existing `complete_json()` for policy judgment, which is untouched),
   falling back to polling `GET /workflow-runs/{run_id}` on exponential
   backoff (2s → 10s cap) up to 90s if the execute call doesn't block.
   Raises `AutoOperatorError` on any failure; the router turns that into
   a 503, not a 500 — **verified**: with no `AUTO_*` env vars set, hitting
   `POST /api/ai-manager/messages` returns a clean 503 naming exactly
   which vars are missing, not a crash.
3. **Built AI Insights from scratch** — `app/schemas/insights.py` +
   `app/services/insights.py`. Deliberately **not** a port of the old
   `insight_engine.py`/`DisruptionRun`-based design mentioned in the
   previous handoff (that source was never actually uploaded to this
   session, only described). Instead: a small, deterministic aggregation
   directly over `PolicyEvaluation` and `WorkbenchItem` — recurring
   policy-match patterns, pending Workbench backlog, escalation counts,
   repeated-resolution → policy-creation opportunities. No LLM call, no
   new DB table/migration (so none of the three recurring bug classes
   from the previous handoff's §5 — `models/__init__.py` omissions,
   migration ID collisions, architecture mismatches — have a chance to
   recur here). **Verified**: created a real policy + fired a real event
   through the Orchestrator in a `TestClient` session, then confirmed
   `GET /api/ai/insights` reflects it (`based_on_records: 2`, a genuine
   "1 item(s) pending in Workbench" insight) — not a static shape.
4. **Fixed the Insights frontend page** —
   `frontend/src/app/ai/insights/page.tsx` was still 100% the old
   `DEMO_INSIGHTS`/`DEMO_PATTERNS`/`DEMO_ACTIONS` version despite a prior
   session's log claiming it had been wired (that specific edit was
   apparently never captured before the tool-call limit hit). Rewritten
   to call `GET /api/ai/insights` and `POST /api/ai/insights/refresh`,
   matching the already-wired Policies page's loading/error-banner
   pattern exactly (`loadError` state + retry button).
5. **Built the AI Manager chat frontend from scratch** —
   `frontend/src/app/ai-manager/page.tsx` did not exist in any uploaded
   file or the base zip. Chat UI: sends `POST /api/ai-manager/messages`,
   renders the assistant reply plus a per-step Orchestrator trace
   (`activity_runs`, each with a status badge) so a judge can watch the
   Orchestrator delegate to Operators, per the problem statement's
   explicit ask. Added to the sidebar under "AI Intelligence".
6. **Re-fixed the `.env.example` vs `env.example` drift bug.** This zip
   still shipped `env.example` (no dot) while `README.md` instructs
   `cp .env.example .env` — same bug the Aug 8 doc already flagged as
   fixed on `origin/main`, but present again in this zip (confirms the
   Aug 8 doc's own warning: multiple non-synced copies of this repo
   exist). Renamed. Also added `AUTO_ORCHESTRATOR_WORKFLOW_ID` to the env
   template (distinct from `AUTO_LLM_JUDGMENT_WORKFLOW_ID` — see below).

## 2. Verification actually run this session (copy-paste ready)

```bash
pip install -r packages/requirements.txt --break-system-packages

rm -f verify.db
DATABASE_URL="sqlite:///./verify.db" AUTH_BYPASS=true python3 -c "
from app.core.database import Base, engine
from app import models
Base.metadata.create_all(bind=engine)
print('tables:', sorted(Base.metadata.tables.keys()))
"
# tables: ['audit_logs', 'data_sources', 'items', 'policies',
#          'policy_evaluations', 'settings', 'workbench_items']  ✅ all 7

DATABASE_URL="sqlite:///./verify.db" AUTH_BYPASS=true python3 -c "
from fastapi.testclient import TestClient
from app.main import app
with TestClient(app) as client:
    r = client.post('/api/ai/policies', json={
        'name': 'test', 'description': '', 'natural_language': 'x',
        'policy_type': 'logical', 'entity_name': 'disruption_notice',
        'dsl': {'conditions': [{'field': 'severity', 'operator': 'equals', 'value': 'critical'}],
                'actions': [{'type': 'require_approval'}], 'match_mode': 'all'}})
    assert r.status_code == 200, r.text
    r = client.post('/api/orchestrator/events', json={
        'entity_name': 'disruption_notice',
        'record': {'id': 'DN-1', 'severity': 'critical'}, 'source': 'test'})
    assert r.status_code == 200 and r.json()['workbench_item'] is not None, r.text
    print('FULL PIPELINE OK')

    r = client.post('/api/ai-manager/messages', json={'message': 'status check'})
    assert r.status_code == 503   # no AUTO_* creds set — clean 503, not a 500
    print('AI MANAGER 503-WITHOUT-CREDS OK')

    r = client.get('/api/ai/insights')
    data = r.json()
    assert data['based_on_records'] >= 2
    print('insights:', [i['title'] for i in data['insights']])
    r = client.post('/api/ai/insights/refresh')
    assert r.status_code == 200
    print('INSIGHTS OK')
"
rm -f verify.db
```
All of the above passed as-run this session. **This sandbox has no
network egress to `auto.supervity.ai`** — everything above is verified
against SQLite + the FastAPI app in-process; nobody has yet made a real
call to the live Orchestrator with real `AUTO_API_KEY`/`AUTO_ORG_KEY`/
`AUTO_ORCHESTRATOR_WORKFLOW_ID`. **That is the next required step**, see
§4 below.

## 3. Submission checklist — updated status

| Item | Status |
|---|---|
| Orchestrator on Auto + 5 Operators | Per your memory/hackathon notes, 7 Operators are built and showing "Ready" on Auto (Ops Helper orchestrator + 6 workers). **Not independently re-verified this session** — this doc can't confirm Auto-side state, only that the backend code to call it now exists and is wired. |
| Command Center wired to backend | **Improved.** AI Manager chat surface now exists, frontend + backend, and is reachable (`/ai-manager` in the sidebar). Still missing: Data Manager frontend page (backend exists, no UI). |
| AI Policies | Unchanged — still fully real and verified. |
| AI Insights from real data | **Now real**, verified this session against actual `PolicyEvaluation`/`WorkbenchItem` rows. No more demo data on the frontend. |
| Live integrations (3+, 2 categories) | Unchanged from Aug 8 — code real, nothing actually live. Still needs a real Supabase project + real channel integration. See §4. |
| Workbench: real exception, human-resolved | Unchanged — still fully real and verified. |
| Clean clone runs | `.env.example` naming re-fixed. Still needs an actual `git clone` + `docker compose up` test on your machine — this sandbox can't run Docker. |
| URLs public | Unverified — no deployment done this session. |

## 4. Concrete next steps, in priority order

1. **Get real `AUTO_API_KEY` / `AUTO_ORG_KEY` / `AUTO_ORCHESTRATOR_WORKFLOW_ID`
   into a real `.env` and hit `POST /api/ai-manager/messages` for real.**
   This is the single highest-value next step — everything else in AI
   Manager is now built and passing every check this sandbox can run,
   but nobody has confirmed `trigger_orchestrator_run()`'s
   execute-or-poll logic actually matches what Auto returns. If the
   shape is wrong, the fix is entirely inside
   `app/services/auto_client.py` — `_format_run_result()` and the
   terminal-status check are the two most likely places to need
   adjusting.
   - Use `AUTO_ORCHESTRATOR_WORKFLOW_ID` = the Ops Helper orchestrator's
     workflow ID (`019f7b51-f797-7000-8761-d325557229fa`, per your
     hackathon notes) — **not** the LLM Judgment workflow ID, they're
     different env vars now (`AUTO_ORCHESTRATOR_WORKFLOW_ID` vs.
     `AUTO_LLM_JUDGMENT_WORKFLOW_ID`).
2. **Build the Data Manager frontend page.** Backend
   (`GET /api/data-manager`) already works; there's still no
   `frontend/src/app/data-manager/page.tsx` and nothing in the sidebar
   links to it. Needed for the "visible... in the Data Manager" checklist
   wording, not just a `curl`-able API.
3. **Make the 3 live integrations actually live** — real Supabase
   project with `disruption_notices.csv` loaded (see the Aug 8 doc's old
   §4.2 for the exact column-name/date-format gotchas, unchanged this
   session), one real channel (Slack/email/Teams).
4. **Clean-clone test on your machine** — `git clone`, follow only the
   README, confirm `make up` / `docker compose up` actually comes up
   with these changes included. This sandbox cannot run Docker.
5. **Deploy and confirm a public URL** judges can reach, plus confirm the
   Auto workspace itself is shared to judges, not just your account.

## 5. Things worth knowing before touching what changed this session

- `app/services/insights.py` is intentionally simple — deterministic
  aggregation, not an LLM call — so it has no dependency on Auto/LLM
  credentials being configured at all. If you want richer, more
  natural-language insight descriptions later, that's a layer to add on
  *top* of these aggregates (e.g. pass them through
  `auto_client.complete_json()` to phrase them), not a replacement — the
  underlying counts/groupings should stay real and queryable regardless.
- `trigger_orchestrator_run()` and `complete_json()` in
  `app/services/auto_client.py` are two genuinely separate Auto
  workflows now, each with its own workflow ID env var
  (`AUTO_ORCHESTRATOR_WORKFLOW_ID` vs `AUTO_LLM_JUDGMENT_WORKFLOW_ID`).
  Don't collapse them back into one — Policy Engine/Insights judgment and
  the AI Manager's orchestrator trigger are different calls with
  different response shapes (`complete_json` returns a parsed dict from
  a single judgment step; `trigger_orchestrator_run` returns a full
  multi-step run trace).
- The frontend Insights page's `InsightsResponse` interface must keep
  matching `app/schemas/insights.py`'s `InsightsResponse` field-for-field
  (same convention as the Policy Card components) — if either side's
  shape changes, update both together.
- Re-run §2's verification after any change to
  `app/services/auto_client.py`, `app/services/insights.py`, or anything
  in `app/models/`/`app/services/triage.py`/`orchestrator_engine.py`/
  `orchestrator_poller.py` (still the recurring-bug files flagged in the
  Aug 8 doc) before considering a change done.

## 6. Instructions for starting the next session

Paste this file in, plus:
- Confirm you're actually working from this zip's contents (or whatever
  supersedes it) before trusting §1/§3 above — same warning as every
  previous version of this doc.
- Priority is §4 item 1: get real Auto credentials into `.env` and make
  one real call through `POST /api/ai-manager/messages`. Nothing else
  blocks that from happening now — the code is written and passing every
  check available without network access to `auto.supervity.ai`.
- If Data Manager frontend (§4 item 2) is next, `app/routers/data_manager.py`
  already returns everything the page needs — this is a pure frontend
  task, no backend changes required.
