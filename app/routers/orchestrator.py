# app/routers/orchestrator.py
"""
Orchestrator ingest endpoint — the manual/external front door for
incoming disruption notices / exceptions. All the actual decision logic
(Policy Engine -> decide -> Workbench) lives in
services/orchestrator_engine.process_event(), shared with the Supabase
poller (services/orchestrator_poller.py) so both triggers guarantee the
same behavior for the same input — this router is just the HTTP wrapper
around it.

Call this endpoint from wherever a manual/external trigger lives:
  - a Slack listener forwarding an inbound Disruption Notice Inbox
    message,
  - the "Simulate Disruption" button on the frontend (frontend/src/app/
    workbench/page.tsx),
  - a real webhook from whatever system actually originates these events.

The Supabase system-of-record table is read automatically by the poller
on its own schedule (services/orchestrator_poller.py) — it does NOT need
this endpoint called for it.
"""
import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..schemas.policy import PolicyEvaluationResult
from ..security import get_current_user
from ..services.orchestrator_engine import process_event

log = logging.getLogger(__name__)

router = APIRouter(prefix="/orchestrator", tags=["Orchestrator"])


class OrchestratorEventRequest(BaseModel):
    """What you send to POST /api/orchestrator/events. `entity_name`
    picks which policies apply — same field evaluate_policies_for_entity()
    already takes. `source` is provenance only (shows up in the audit log
    and, if this routes to Workbench, on the resulting item's `source`
    field) — it doesn't change the decision."""

    entity_name: str
    record: dict = Field(default_factory=dict)
    source: str = "orchestrator"  # e.g. "slack" | "manual" | "manual_simulation"
    notify_target: str | None = None  # passed straight through to route_to_workbench if it fires


class OrchestratorEventResponse(BaseModel):
    entity_name: str
    policy_evaluations: list[PolicyEvaluationResult]
    matched_count: int
    decision: str  # "auto_resolve" | "route_to_workbench"
    priority: str
    reasoning: str
    workbench_item_id: str | None = None


@router.post("/events", response_model=OrchestratorEventResponse)
async def ingest_event(
    payload: OrchestratorEventRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """The one endpoint this router exists for: hand it an incoming
    disruption notice / exception record and it runs the Policy Engine,
    decides what to do with the result, and — only if a human is
    actually needed — creates the Workbench item itself. See
    services/orchestrator_engine.process_event() for the actual logic —
    the Supabase poller calls that same function directly, without going
    through HTTP. Manual calls here never dedupe (dedupe=False) — this
    endpoint (and the Simulate Disruption button) is legitimately called
    repeatedly with similar/identical records for demo purposes."""
    result = await process_event(
        db,
        entity_name=payload.entity_name,
        record=payload.record,
        source=payload.source,
        notify_target=payload.notify_target,
        actor=user,
        request=request,
        dedupe=False,
    )
    return OrchestratorEventResponse(
        entity_name=result.entity_name,
        policy_evaluations=result.policy_evaluations,
        matched_count=result.matched_count,
        decision=result.decision,
        priority=result.priority,
        reasoning=result.reasoning,
        workbench_item_id=result.workbench_item_id,
    )
