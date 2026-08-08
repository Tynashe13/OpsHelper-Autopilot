# app/services/orchestrator_engine.py
"""
Shared Orchestrator processing logic — the one place "what happens to an
incoming record" is decided, used by BOTH:

  - routers/orchestrator.py's POST /api/orchestrator/events (manual/
    external trigger — a Slack forwarder, a "simulate an event" button)
  - services/orchestrator_poller.py (automatic — reads new rows off the
    Supabase system-of-record table on an interval)

Before this file existed, that logic lived directly inside the router,
which meant the poller would have needed its own copy — exactly the kind
of duplicated decision path that lets the two callers drift out of sync
over time. Now both call process_event() below; nothing about "how an
event becomes a policy evaluation, a triage decision, and maybe a
Workbench item" is decided anywhere else.
"""
import logging
from typing import Optional

from fastapi import Request
from sqlalchemy.orm import Session

from .policy_engine import evaluate_policies_for_entity
from .triage import triage_evaluation

log = logging.getLogger(__name__)


async def process_event(
    db: Session,
    *,
    entity_name: str,
    record: dict,
    source: Optional[str] = None,
    actor: Optional[dict] = None,
    request: Optional[Request] = None,
):
    """
    Runs one record through the full chain: Policy Engine evaluation ->
    Triage decision -> Workbench item creation (if Triage calls for one).

    Returns (evaluations, action, reason, workbench_item) — the same four
    values routers/orchestrator.py already unpacked before this refactor,
    so callers on both sides (the router, the poller) get everything they
    need to either build an HTTP response or write a log line, without
    this function knowing or caring which.
    """
    evaluations = await evaluate_policies_for_entity(
        db, entity_name, record, actor=actor, request=request
    )
    action, reason, workbench_item = await triage_evaluation(
        db,
        entity_name=entity_name,
        record=record,
        evaluations=evaluations,
        source=source,
        actor=actor,
        request=request,
    )
    return evaluations, action, reason, workbench_item
