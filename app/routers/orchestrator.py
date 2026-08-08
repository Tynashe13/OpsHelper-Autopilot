# app/routers/orchestrator.py
"""
Orchestrator — the live ingest endpoint. This is what actually POPULATES
Workbench from a real event, closing the loop:

    incoming event -> Policy Engine (evaluate_policies_for_entity) ->
    Triage (decide auto_resolve / route_to_workbench / escalate) ->
    Workbench (created if a human decision is needed)

Call POST /api/orchestrator/events from wherever your manual/external
trigger lives (a Slack forwarder, a "simulate an event" button on the
frontend during development). The SAME chain also runs automatically —
see services/orchestrator_poller.py, which reads new rows off the
Supabase system-of-record table on an interval and feeds each one
through services/orchestrator_engine.process_event(), the exact function
this router calls below. This router stays a thin wrapper specifically
so the manual and automatic paths can never drift apart.
"""
import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..schemas.workbench import (
    OrchestratorEventRequest,
    OrchestratorEventResponse,
    WorkbenchItemResponse,
)
from ..security import get_current_user
from ..services.orchestrator_engine import process_event

log = logging.getLogger(__name__)

router = APIRouter(prefix="/orchestrator", tags=["Orchestrator"])


@router.post("/events", response_model=OrchestratorEventResponse)
async def ingest_event(
    payload: OrchestratorEventRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    THE live trigger. `entity_name` picks which policies apply (see
    services/policy_engine.py's query filter); `record` is the actual
    event/business data. Returns the full trace: what policies matched,
    what Triage decided, and the resulting Workbench item if one was
    created — so a caller (or the frontend, during a demo) can see the
    whole chain from one response instead of piecing it together from
    three separate endpoints.
    """
    evaluations, action, reason, workbench_item = await process_event(
        db,
        entity_name=payload.entity_name,
        record=payload.record,
        source=payload.source or "orchestrator.events",
        actor=user,
        request=request,
    )

    auto_resolution = None
    if action == "auto_resolve":
        matched = [e for e in evaluations if e.matched]
        auto_resolution = {
            "matched_policies": [{"id": e.policy_id, "name": e.policy_name} for e in matched],
            "reason": reason,
        }

    return OrchestratorEventResponse(
        entity_name=payload.entity_name,
        matched_count=sum(1 for e in evaluations if e.matched),
        evaluations=[e.dict() for e in evaluations],
        triage_action=action,
        triage_reason=reason,
        # Explicit ORM -> Pydantic conversion here, rather than handing the
        # raw SQLAlchemy WorkbenchItem to the response model's constructor:
        # `orm_mode`/`from_attributes` on WorkbenchItemResponse only kicks
        # in automatically when a route returns the ORM object directly as
        # its top-level response_model (as routers/ai_policies.py does),
        # not when it's nested inside another schema being built manually
        # like this one — so it needs an explicit
        # `model_validate(..., from_attributes=True)` call.
        workbench_item=WorkbenchItemResponse.model_validate(workbench_item, from_attributes=True)
        if workbench_item
        else None,
        auto_resolution=auto_resolution,
    )
