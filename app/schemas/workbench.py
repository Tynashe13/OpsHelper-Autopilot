# app/schemas/workbench.py
"""
Pydantic schemas for the Workbench pillar. Same split as schemas/policy.py
and schemas/data_source.py: these describe API request/response shapes,
NOT the database (that's models/workbench.py's job).

Grouped the same way policy.py is:
  1. Core CRUD/read shapes
  2. The "route something to Workbench" entry point (this is what
     satisfies "1+ real exception routed to Workbench" — the part judges
     are scoring)
  3. Resolution/escalation action shapes
"""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Core CRUD / read shapes
# ---------------------------------------------------------------------------


class WorkbenchItemBase(BaseModel):
    title: str
    description: str = ""
    entity_name: Optional[str] = None
    entity_id: Optional[str] = None
    source: str = "manual"
    payload: Optional[dict] = None
    reason: Optional[str] = None
    priority: str = "medium"  # 'low' | 'medium' | 'high' | 'critical'
    notify_target: Optional[str] = None
    max_retries: int = 3
    retry_interval_minutes: int = 15


class WorkbenchItemUpdate(BaseModel):
    """PATCH body — every field optional, same reasoning as
    DataSourceUpdate/PolicyUpdate: a client should be able to update just
    one field without resending the whole object."""

    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    assigned_to: Optional[str] = None
    notify_target: Optional[str] = None


class WorkbenchItemResponse(BaseModel):
    id: str
    title: str
    description: str
    entity_name: Optional[str] = None
    entity_id: Optional[str] = None
    source: str
    payload: Optional[dict] = None
    reason: Optional[str] = None
    status: str
    priority: str
    notify_target: Optional[str] = None
    assigned_to: Optional[str] = None
    retry_count: int
    max_retries: int
    retry_interval_minutes: int
    last_notified_at: Optional[datetime] = None
    next_retry_at: Optional[datetime] = None
    escalated: bool
    escalated_to: Optional[str] = None
    escalated_at: Optional[datetime] = None
    resolution: Optional[str] = None
    resolved_by: Optional[str] = None
    resolved_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True


class WorkbenchSummary(BaseModel):
    """Powers KPI cards, same idea as DataSourceSummary — computed, not a
    table. See GET /workbench/summary in routers/workbench.py."""

    total: int
    pending: int
    in_progress: int
    resolved: int
    escalated: int
    cancelled: int
    by_priority: dict[str, int]


# ---------------------------------------------------------------------------
# Routing entry point — what the Policy Engine / Orchestrator-integration
# layer calls when something needs a human. This is the part that satisfies
# "1+ real exception routed to Workbench, resolved by a human".
# ---------------------------------------------------------------------------


class RouteToWorkbenchRequest(WorkbenchItemBase):
    """What you POST to /api/workbench to create a new item. Identical
    fields to WorkbenchItemBase today (hence no extra fields here) but
    kept as its own class, same reasoning as PolicyCreate/DataSourceCreate:
    room to add routing-only fields later without touching the read shape."""

    pass


# ---------------------------------------------------------------------------
# Resolution / escalation actions
# ---------------------------------------------------------------------------


class ResolveWorkbenchItemRequest(BaseModel):
    """What a human sends when they close an item out. `resolution` is
    required — an item shouldn't be marked resolved with no record of
    what was decided."""

    resolution: str
    actions_taken: list[dict[str, Any]] = Field(default_factory=list)


class EscalateWorkbenchItemRequest(BaseModel):
    """Manual escalation (as opposed to the scheduler's automatic one in
    services/workbench_scheduler.py, which fires after max_retries)."""

    escalated_to: str
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Orchestrator ingest — routers/orchestrator.py's request/response shapes live
# here rather than a separate schemas/orchestrator.py because there's no
# orchestrator-specific data model of its own; every field on
# OrchestratorEventResponse is either raw pass-through or a Workbench shape
# above — there's no orchestrator-specific data model of its own.
# ---------------------------------------------------------------------------


class OrchestratorEventRequest(BaseModel):
    """What you POST to /api/orchestrator/events. `entity_name` picks
    which policies apply (see policy_engine.py's query filter — identical
    meaning to EvaluateRequest.entity_name in schemas/policy.py).
    `record` is the actual business data — a recovery plan's cost/timing,
    a disruption notice's fields, whatever `entity_name` represents.
    `source` is a free-text label for where this event came from (e.g.
    "orchestrator.events" for a manual/API call, "supabase_poller" for
    the automatic poller) — stored on the resulting audit log entries and
    Workbench item, purely for traceability."""

    entity_name: str
    record: dict
    source: Optional[str] = None


class OrchestratorEventResponse(BaseModel):
    """The full trace of what happened to one event: every policy
    evaluation that ran, Triage's decision, and the resulting Workbench
    item if one was created — so a caller (or the frontend's "Simulate
    Disruption" button) can see the whole chain from one response instead
    of piecing it together from three separate endpoints."""

    entity_name: str
    matched_count: int
    evaluations: list[dict]
    triage_action: str  # 'auto_resolve' | 'route_to_workbench' | 'escalate' | 'no_action'
    triage_reason: str
    workbench_item: Optional[WorkbenchItemResponse] = None
    auto_resolution: Optional[dict] = None
