# app/routers/orchestrator.py
"""
Orchestrator ingest endpoint — the live front door for incoming
disruption notices / exceptions, and the piece that was still missing:
Policy Engine and Workbench existed as two separate, working pillars, but
nothing called evaluate_policies_for_entity() and then decided whether to
call services/triage.route_to_workbench() based on what it found. This
router is that wire.

Call it from wherever the real event source is:
  - Auto's live Orchestrator workflow (once wired), OR
  - a Slack listener forwarding an inbound Disruption Notice Inbox
    message, OR
  - a "simulate disruption" trigger from the frontend/a test script,
    while the above two aren't wired to a live source yet.

Decision logic (_decide, below) is deliberately simple and transparent —
it reads the `type` field off each matched policy's actions (the same
PolicyAction.type strings the Policy Builder UI already writes, e.g.
"require_approval", "auto_execute") rather than re-interpreting anything
the Policy Engine already decided. It does not call an LLM itself: the
Policy Engine's own natural_language path already made an LLM call if the
matched policy needed one (see services/policy_engine.py's
_evaluate_natural_language) — Triage only has to read the verdict, not
form a new one.
"""
import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..schemas.policy import PolicyAction, PolicyEvaluationResult
from ..security import get_current_user
from ..services.audit import audit
from ..services.policy_engine import evaluate_policies_for_entity
from ..services.triage import route_to_workbench

log = logging.getLogger(__name__)

router = APIRouter(prefix="/orchestrator", tags=["Orchestrator"])

# Action `type` strings (see schemas/policy.py's PolicyAction and the
# Policy Builder UI) that mean "a human has to weigh in" vs. "the system
# is cleared to proceed on its own." Anything matched that isn't in
# either set is treated conservatively — see _decide() below.
_HUMAN_REQUIRED_TYPES = {"require_approval", "escalate", "block", "veto"}
_AUTO_CLEAR_TYPES = {"auto_execute", "approve", "auto_approve"}

# Cost/amount fields checked for a severity->priority heuristic when a
# record has no explicit priority — same MYR-scale reasoning already used
# by the seed policies in alembic/versions/e5f6g7h8i9j0_add_policies_tables.py.
_AMOUNT_FIELDS = ("estimated_cost", "amount", "cost", "value")


def _infer_priority(record: dict, evaluations: list[PolicyEvaluationResult]) -> str:
    explicit = record.get("priority") or record.get("severity")
    if isinstance(explicit, str) and explicit.lower() in ("low", "medium", "high", "critical"):
        return explicit.lower()

    for field in _AMOUNT_FIELDS:
        raw = record.get(field)
        if raw is None:
            continue
        try:
            amount = float(raw)
        except (TypeError, ValueError):
            continue
        if amount >= 20000:
            return "critical"
        if amount >= 5000:
            return "high"
        if amount >= 1000:
            return "medium"
        return "low"

    for ev in evaluations:
        if ev.matched and any(a.type in _HUMAN_REQUIRED_TYPES for a in ev.actions):
            return "high"
    return "medium"


def _decide(
    entity_name: str,
    record: dict,
    evaluations: list[PolicyEvaluationResult],
) -> tuple[str, str, str, list[PolicyAction]]:
    """
    Returns (decision, priority, reasoning, matched_actions) where
    decision is one of "auto_resolve" | "route_to_workbench". There is no
    separate "escalate" decision here — a priority of "critical" is what
    tells route_to_workbench() to notify urgently (via `priority`); actual
    escalation (retries exhausted -> different target) is
    services/workbench_scheduler.py's job, not this endpoint's.
    """
    matched = [e for e in evaluations if e.matched]
    all_actions = [a for e in matched for a in e.actions]
    action_types = {a.type for a in all_actions}
    priority = _infer_priority(record, evaluations)

    needs_human = bool(action_types & _HUMAN_REQUIRED_TYPES) or priority in ("high", "critical")
    has_auto_clear = bool(action_types & _AUTO_CLEAR_TYPES)

    if matched and needs_human:
        reasoning = "; ".join(f"'{e.policy_name}': {e.explanation}" for e in matched if e.explanation) or (
            f"A matched policy requires human approval for this {entity_name} record."
        )
        return "route_to_workbench", priority, reasoning, all_actions

    if matched and has_auto_clear and not needs_human:
        reasoning = "; ".join(f"'{e.policy_name}': {e.explanation}" for e in matched if e.explanation) or (
            f"Matched policy authorizes automatic execution for this {entity_name} record."
        )
        return "auto_resolve", priority, reasoning, all_actions

    if not matched:
        # No policy covers this case — per the same fail-safe reasoning
        # policy_engine.py already uses for a failed LLM call ("treated
        # as no-match" rather than assumed-safe), an uncovered case goes
        # to a human, not silently through.
        reasoning = f"No active policy matched this {entity_name} record — routing to a human because no rule covers this case."
        return "route_to_workbench", priority, reasoning, all_actions

    # Matched, but nothing in the matched actions was an explicit
    # auto-clear or an explicit approval requirement (e.g. a policy that
    # only tags/logs something). Default to human review.
    reasoning = "Policy matched but specified no auto-execute action — defaulting to human review."
    return "route_to_workbench", priority, reasoning, all_actions


class OrchestratorEventRequest(BaseModel):
    """What you send to POST /api/orchestrator/events. `entity_name`
    picks which policies apply — same field evaluate_policies_for_entity()
    already takes. `source` is provenance only (shows up in the audit log
    and, if this routes to Workbench, on the resulting item's `source`
    field) — it doesn't change the decision."""

    entity_name: str
    record: dict = Field(default_factory=dict)
    source: str = "orchestrator"  # e.g. "auto_orchestrator" | "slack" | "manual"
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
    actually needed — creates the Workbench item itself."""
    await audit.log(
        action="orchestrator.event_received",
        description=f"Orchestrator event received for entity '{payload.entity_name}' from source '{payload.source}'",
        actor=user,
        category="data",
        resource_type=payload.entity_name,
        resource_id=str(payload.record.get("id", "")) or None,
        metadata={"source": payload.source},
        request=request,
    )

    evaluations = await evaluate_policies_for_entity(
        db, payload.entity_name, payload.record, actor=user, request=request
    )
    decision, priority, reasoning, matched_actions = _decide(payload.entity_name, payload.record, evaluations)

    await audit.log(
        action="orchestrator.decide",
        description=f"Orchestrator decided '{decision}' for {payload.entity_name} (priority={priority})",
        actor=user,
        category="data",
        resource_type=payload.entity_name,
        resource_id=str(payload.record.get("id", "")) or None,
        metadata={
            "decision": decision,
            "priority": priority,
            "matched_policies": [e.policy_name for e in evaluations if e.matched],
        },
        request=request,
    )

    workbench_item_id = None
    if decision == "route_to_workbench":
        title = str(payload.record.get("title") or payload.record.get("name") or f"{payload.entity_name} exception")[:500]
        item = await route_to_workbench(
            db,
            title=title,
            description=reasoning,
            entity_name=payload.entity_name,
            entity_id=str(payload.record.get("id", "")) or None,
            source=payload.source,
            payload=payload.record,
            reason=reasoning,
            priority=priority,
            notify_target=payload.notify_target,
            actor=user,
        )
        workbench_item_id = item.id

    return OrchestratorEventResponse(
        entity_name=payload.entity_name,
        policy_evaluations=evaluations,
        matched_count=sum(1 for e in evaluations if e.matched),
        decision=decision,
        priority=priority,
        reasoning=reasoning,
        workbench_item_id=workbench_item_id,
    )
