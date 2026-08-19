# app/routers/dashboard.py
"""
Dashboard router — backs the Dashboard page's live KPIs
(frontend/src/app/page.tsx). That frontend file was handed over as a
fixed target: this file's job is producing exactly the JSON shape it
already expects (see schemas/dashboard.py), computed from data this
app's actual architecture produces — not the DisruptionRun-per-event
model the frontend was originally paired with.

WHERE EACH FIELD ACTUALLY COMES FROM (read this before changing anything
below — every mapping here was a deliberate choice, not a guess):

- There's no `disruption_runs` table in this architecture. The closest
  equivalent is the AuditLog entries services/orchestrator_engine.py's
  process_event() writes under action="orchestrator.decide" — ONE entry
  per event processed, success or failure, whether it auto-resolved or
  got routed to a human. This is what makes "auto_executed" and "failed"
  countable at all: an auto-resolved event never creates a WorkbenchItem
  row, so without this audit entry it would leave literally no trace.
- "pending_approval" = WorkbenchItem rows currently PENDING/IN_PROGRESS/
  ESCALATED (i.e. still awaiting a human) — a live snapshot, not a
  historical count, since an item's status can change after the
  orchestrator.decide entry that created it was written.
- "approved"/"rejected" = resolved WorkbenchItem rows, disambiguated by
  looking at the actual resolve action recorded in the corresponding
  "workbench.resolve" audit entry's actions_taken (WorkbenchItem itself
  doesn't persist an approve/reject flag — see routers/workbench.py's
  resolve endpoint, which only stores freeform `resolution` text on the
  item and puts actions_taken in the audit log instead).
- "running" is always 0 here — this system processes an event fully
  within one process_event() call; nothing is ever left "in progress"
  the way a long-running async Orchestrator job would be. Kept in the
  response because the frontend's AgentStats type expects the field, not
  because this architecture has anything real to put there.
- cost_avoided / time_saved_hours are always null. The frontend's fields
  came from Ops Helper's LLM-generated output (estimated_cost_avoided,
  time_saved_hours); this app's Triage doesn't produce comparable
  estimates for a resolved item, so returning null (honest "we don't
  track this") is correct — not a bug to fix by inventing a number.
- supabase_counts uses services/system_of_record.count_table_rows()
  against this app's actual Supabase connection (core/supabase_db.py —
  a direct Postgres connection, not the REST API the original frontend's
  paired backend used) — same fail-soft-per-table behavior either way:
  null (not 0) when unconfigured or a query fails, real numbers when it
  works.
"""
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..models.audit import AuditLog
from ..models.workbench import WorkbenchItem, WorkbenchStatus
from ..schemas.dashboard import (
    AgentStats,
    DailyRunCount,
    DashboardSummary,
    RecentRun,
    SupabaseLiveCounts,
)
from ..services.orchestrator_engine import ORCHESTRATOR_DECIDE_ACTION
from ..services.system_of_record import count_table_rows

log = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

_ALLOWED_DAY_RANGES = {7, 14, 30}
_NON_TERMINAL_WORKBENCH_STATUSES = (
    WorkbenchStatus.PENDING,
    WorkbenchStatus.IN_PROGRESS,
    WorkbenchStatus.ESCALATED,
)
_RECENT_RUNS_LIMIT = 20


def _resolve_action_types(db: Session, item_ids: list[str]) -> dict[str, set[str]]:
    """Batch-fetches every 'workbench.resolve' audit entry for the given
    item ids in one query, returning {item_id: {action_type, ...}} —
    avoids an N+1 query (one lookup per resolved item) when building the
    recent-runs list and the approved/rejected counts."""
    if not item_ids:
        return {}
    logs = (
        db.query(AuditLog)
        .filter(AuditLog.action == "workbench.resolve", AuditLog.resource_id.in_(item_ids))
        .all()
    )
    result: dict[str, set[str]] = defaultdict(set)
    for entry in logs:
        actions_taken = (entry.extra_data or {}).get("actions_taken") or []
        for action in actions_taken:
            action_type = str(action.get("type", "")).lower()
            if action_type:
                result[entry.resource_id].add(action_type)
    return result


def _run_status_for_item(item: WorkbenchItem, resolve_actions: dict[str, set[str]]) -> str:
    """Maps a WorkbenchItem's real state onto the frontend's RunStatus
    union. A resolved item's approve/reject split comes from the actual
    resolve action recorded in the audit log (see
    _resolve_action_types) — never guessed from the freeform
    `resolution` text."""
    if item.status in _NON_TERMINAL_WORKBENCH_STATUSES:
        return "pending_approval"
    if item.status == WorkbenchStatus.RESOLVED:
        actions = resolve_actions.get(item.id, set())
        if any("reject" in a for a in actions):
            return "rejected"
        if any("approve" in a for a in actions):
            return "approved"
        return "approved"  # resolved with no explicit reject action recorded — treat as approved, not unknown
    return "rejected"  # CANCELLED or anything else terminal-but-not-a-clean-approval


@router.get("/summary", response_model=DashboardSummary)
async def get_dashboard_summary(days: int = 7, db: Session = Depends(get_db)):
    if days not in _ALLOWED_DAY_RANGES:
        days = 7
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    decide_logs = (
        db.query(AuditLog)
        .filter(AuditLog.action == ORCHESTRATOR_DECIDE_ACTION, AuditLog.timestamp >= cutoff)
        .order_by(AuditLog.timestamp.desc())
        .all()
    )

    # --- Live Workbench snapshot (not windowed by `days` — a human might
    # resolve something today that was created outside the window, and
    # the KPI tiles should reflect current reality, not a stale count) ---
    workbench_items = db.query(WorkbenchItem).all()
    resolved_ids = [i.id for i in workbench_items if i.status == WorkbenchStatus.RESOLVED]
    resolve_actions = _resolve_action_types(db, resolved_ids)

    pending_approval = sum(1 for i in workbench_items if i.status in _NON_TERMINAL_WORKBENCH_STATUSES)
    approved = sum(
        1 for i in workbench_items
        if i.status == WorkbenchStatus.RESOLVED and _run_status_for_item(i, resolve_actions) == "approved"
    )
    rejected = sum(
        1 for i in workbench_items
        if i.status == WorkbenchStatus.RESOLVED and _run_status_for_item(i, resolve_actions) == "rejected"
    )

    auto_executed = sum(
        1 for e in decide_logs if e.success == "true" and (e.extra_data or {}).get("decision") == "auto_resolve"
    )
    failed = sum(1 for e in decide_logs if e.success != "true")

    agent_stats = AgentStats(
        total=len(decide_logs),
        pending_approval=pending_approval,
        auto_executed=auto_executed,
        approved=approved,
        rejected=rejected,
        failed=failed,
        running=0,  # see module docstring — nothing is ever "in progress" in this architecture
    )

    # --- Daily chart: bucket orchestrator.decide entries by day, filling
    # every day in the window (including zero-run days) so the chart
    # doesn't silently skip gaps ---
    counts_by_day: dict[str, int] = defaultdict(int)
    for entry in decide_logs:
        if entry.timestamp:
            counts_by_day[entry.timestamp.date().isoformat()] += 1
    today = datetime.now(timezone.utc).date()
    daily_run_counts = [
        DailyRunCount(date=(today - timedelta(days=i)).isoformat(), count=counts_by_day.get((today - timedelta(days=i)).isoformat(), 0))
        for i in range(days - 1, -1, -1)
    ]

    # --- Recent runs: newest orchestrator.decide entries, each resolved
    # to a real RunStatus by checking the live WorkbenchItem it produced
    # (if any) ---
    workbench_by_id = {i.id: i for i in workbench_items}
    recent_runs: list[RecentRun] = []
    for entry in decide_logs[:_RECENT_RUNS_LIMIT]:
        extra = entry.extra_data or {}
        decision = extra.get("decision", "auto_resolve")
        if entry.success != "true":
            status = "failed"
        elif decision == "auto_resolve":
            status = "auto_executed"
        else:
            item = workbench_by_id.get(extra.get("workbench_item_id"))
            status = _run_status_for_item(item, resolve_actions) if item else "pending_approval"

        label = entry.resource_name or (
            f"{extra.get('entity_name', 'event')} {entry.resource_id[:8]}" if entry.resource_id else "Unknown event"
        )
        recent_runs.append(
            RecentRun(
                id=str(entry.resource_id or entry.id),
                supplier_label=str(label),
                status=status,
                created_at=entry.timestamp,
                cost_avoided=None,   # see module docstring — no equivalent estimate in this architecture
                time_saved_hours=None,
            )
        )

    # --- Live Supabase counts — real per-table SELECT COUNT(*), fail-soft
    # per table, same env-var names as the original paired frontend used
    # so a .env from that setup still works unchanged ---
    suppliers_count = count_table_rows("SUPABASE_SUPPLIERS_TABLE", "Suppliers")
    notices_count = count_table_rows("SUPABASE_DISRUPTION_NOTICES_TABLE", "Disruption_Notices")
    inventory_count = count_table_rows("SUPABASE_INVENTORY_TABLE", "Inventory_Positions")
    shipments_count = count_table_rows("SUPABASE_SHIPMENTS_TABLE", "Shipments")

    supabase_counts = SupabaseLiveCounts(
        suppliers=suppliers_count,
        disruption_notices=notices_count,
        inventory_positions=inventory_count,
        shipments=shipments_count,
        configured=bool(os.getenv("SUPABASE_DB_URL")),
    )

    return DashboardSummary(
        agent_stats=agent_stats,
        cost_avoided_total=None,  # see module docstring
        time_saved_hours_total=None,
        daily_run_counts=daily_run_counts,
        recent_runs=recent_runs,
        supabase_counts=supabase_counts,
    )
