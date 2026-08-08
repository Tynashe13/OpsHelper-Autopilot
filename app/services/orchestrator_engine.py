# app/services/orchestrator_engine.py
"""
Shared Orchestrator decision + processing logic.

This is the ONE place "what happens to an incoming record" gets decided —
extracted out of routers/orchestrator.py so that both the manual/external
trigger (POST /api/orchestrator/events) AND the Supabase poller
(services/orchestrator_poller.py) call this exact same function for the
exact same input and get the exact same behavior. Before this refactor,
having two callers meant two chances for the decision logic to quietly
drift apart; now there's one function and two thin callers.

Pipeline: evaluate_policies_for_entity() (Policy Engine) -> _decide()
(pure function, no I/O, cheap to test in isolation) -> route_to_workbench()
(Workbench) only if a human is actually needed.
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

from fastapi import Request
from sqlalchemy.orm import Session

from ..models.workbench import WorkbenchItem
from ..schemas.policy import PolicyAction, PolicyEvaluationResult
from .audit import audit
from .policy_engine import evaluate_policies_for_entity
from .triage import route_to_workbench

log = logging.getLogger(__name__)

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


@dataclass
class OrchestratorResult:
    entity_name: str
    policy_evaluations: list[PolicyEvaluationResult]
    matched_count: int
    decision: str  # "auto_resolve" | "route_to_workbench" | "skipped_duplicate"
    priority: str
    reasoning: str
    workbench_item_id: Optional[str] = None
    matched_actions: list[PolicyAction] = field(default_factory=list)


def _infer_priority(record: dict, evaluations: list[PolicyEvaluationResult]) -> str:
    explicit = record.get("priority") or record.get("severity")
    if isinstance(explicit, str) and explicit.lower() in ("low", "medium", "high", "critical"):
        return explicit.lower()

    for f in _AMOUNT_FIELDS:
        raw = record.get(f)
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
    Pure function, no I/O — cheap to test exhaustively (see
    tests/test_orchestrator.py's plan: no-match, human-required,
    auto-resolve, matched-but-no-auto-action). Returns
    (decision, priority, reasoning, matched_actions) where decision is
    one of "auto_resolve" | "route_to_workbench". There is no separate
    "escalate" decision here — a priority of "critical" is what tells
    route_to_workbench() to notify urgently; retries-exhausted escalation
    is services/workbench_scheduler.py's job, not this function's.
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
        reasoning = f"No active policy matched this {entity_name} record — routing to a human because no rule covers this case."
        return "route_to_workbench", priority, reasoning, all_actions

    reasoning = "Policy matched but specified no auto-execute action — defaulting to human review."
    return "route_to_workbench", priority, reasoning, all_actions


async def process_event(
    db: Session,
    *,
    entity_name: str,
    record: dict,
    source: str = "orchestrator",
    notify_target: Optional[str] = None,
    actor: Optional[dict] = None,
    request: Optional[Request] = None,
    dedupe: bool = False,
) -> OrchestratorResult:
    """
    THE function this module exists to expose. Runs Policy Engine ->
    _decide() -> Workbench (if needed) for one record.

    `dedupe`, when True, skips processing (and returns decision=
    "skipped_duplicate") if a WorkbenchItem already exists for this exact
    (entity_name, entity_id, source) triple. Off by default — the manual
    /api/orchestrator/events endpoint and the "Simulate Disruption" button
    legitimately want to be callable repeatedly with the same record for
    demo purposes. The Supabase poller (services/orchestrator_poller.py)
    turns this ON, as a second, independent line of defense: a poller
    tick can be re-triggered (process restart, a cursor that ends up
    stuck, a manual re-run) and must never be able to create duplicate
    Workbench items for a row it already processed, regardless of
    whether the cursor math is airtight — belt-and-suspenders, not a
    substitute for a correct cursor.
    """
    record_id = str(record.get("id", "")) or None

    if dedupe and record_id:
        already = (
            db.query(WorkbenchItem)
            .filter(
                WorkbenchItem.entity_name == entity_name,
                WorkbenchItem.entity_id == record_id,
                WorkbenchItem.source == source,
            )
            .first()
        )
        if already is not None:
            log.info(
                "orchestrator_engine: skipping already-processed record entity_name=%s entity_id=%s source=%s",
                entity_name, record_id, source,
            )
            return OrchestratorResult(
                entity_name=entity_name,
                policy_evaluations=[],
                matched_count=0,
                decision="skipped_duplicate",
                priority="low",
                reasoning=f"Already processed (existing Workbench item {already.id}) — skipped to avoid a duplicate.",
                workbench_item_id=already.id,
            )

    await audit.log(
        action="orchestrator.event_received",
        description=f"Orchestrator event received for entity '{entity_name}' from source '{source}'",
        actor=actor,
        category="data",
        resource_type=entity_name,
        resource_id=record_id,
        metadata={"source": source},
        request=request,
    )

    evaluations = await evaluate_policies_for_entity(db, entity_name, record, actor=actor, request=request)
    decision, priority, reasoning, matched_actions = _decide(entity_name, record, evaluations)

    await audit.log(
        action="orchestrator.decide",
        description=f"Orchestrator decided '{decision}' for {entity_name} (priority={priority})",
        actor=actor,
        category="data",
        resource_type=entity_name,
        resource_id=record_id,
        metadata={
            "decision": decision,
            "priority": priority,
            "matched_policies": [e.policy_name for e in evaluations if e.matched],
        },
        request=request,
    )

    workbench_item_id = None
    if decision == "route_to_workbench":
        title = str(record.get("title") or record.get("name") or f"{entity_name} exception")[:500]
        item = await route_to_workbench(
            db,
            title=title,
            description=reasoning,
            entity_name=entity_name,
            entity_id=record_id,
            source=source,
            payload=record,
            reason=reasoning,
            priority=priority,
            notify_target=notify_target,
            actor=actor,
        )
        workbench_item_id = item.id

    return OrchestratorResult(
        entity_name=entity_name,
        policy_evaluations=evaluations,
        matched_count=sum(1 for e in evaluations if e.matched),
        decision=decision,
        priority=priority,
        reasoning=reasoning,
        workbench_item_id=workbench_item_id,
        matched_actions=matched_actions,
    )
