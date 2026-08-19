# app/services/orchestrator_engine.py
"""
Shared Orchestrator processing logic — the one place "what happens to an
incoming record" is decided, used by BOTH:

  - routers/orchestrator.py's POST /api/orchestrator/events (manual/
    external trigger — a Slack forwarder, a "Simulate Disruption" button)
  - services/orchestrator_poller.py (automatic — reads new rows off the
    Supabase system-of-record table on an interval)

Before this file existed, this logic lived directly inside the router,
which would have meant either duplicating it in the poller (the two
triggers silently drifting apart over time) or the poller making an HTTP
call to its own app's endpoint (unnecessary network hop for an in-process
call). Now both call process_event() below.

Fixed during the Round 3 merge: a prior version of this file (and
routers/orchestrator.py) called `triage.triage_evaluation()` and returned
an `(evaluations, action, reason, workbench_item)` tuple — but
services/triage.py only ever defined `route_to_workbench()`, a
differently-shaped function (priority-based, not severity-based) that
every OTHER caller in this codebase (routers/workbench.py,
workbench_scheduler.py) already uses. That mismatch meant this file (and
the router importing from it) failed to import at all — caught by
actually running `TestClient` against the app, not by reading the code.
This version calls the real, existing route_to_workbench() and matches
schemas/workbench.py's actual fields (priority/notify_target/payload/
reason), consistent with the rest of the Workbench pillar.
"""
import logging
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.orm import Session

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
    """What process_event() hands back to either caller. A plain
    dataclass, not a Pydantic model — this is an internal service return
    type; routers/orchestrator.py builds its own Pydantic
    OrchestratorEventResponse from this, and the poller just logs it."""

    entity_name: str
    policy_evaluations: list[PolicyEvaluationResult]
    matched_count: int
    decision: str  # "auto_resolve" | "route_to_workbench"
    priority: str
    reasoning: str
    workbench_item_id: str | None = None
    record_id: str | None = None


def _infer_priority(record: dict, evaluations: list[PolicyEvaluationResult]) -> str:
    explicit = record.get("priority") or record.get("severity")
    if isinstance(explicit, str) and explicit.lower() in ("low", "medium", "high", "critical"):
        return explicit.lower()

    for field_name in _AMOUNT_FIELDS:
        raw = record.get(field_name)
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
    tells route_to_workbench() to notify urgently; actual escalation
    (retries exhausted -> different target) is
    services/workbench_scheduler.py's job, not this function's.
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


ORCHESTRATOR_DECIDE_ACTION = "orchestrator.decide"
"""The audit action string every orchestrator outcome — success or
failure — is logged under. This is deliberately the ONLY durable record
of an auto_resolve decision: unlike route_to_workbench(), auto-resolving
never creates a WorkbenchItem row, so without this audit entry an
auto-resolved event would leave no trace anywhere in the database at
all. routers/dashboard.py's AgentStats/daily-counts/recent-runs are all
built by querying AuditLog for this exact action string — see that
file's docstring for the full mapping."""


async def process_event(
    db: Session,
    *,
    entity_name: str,
    record: dict,
    source: str,
    notify_target: str | None = None,
    actor: dict | None = None,
    request: Request | None = None,
) -> OrchestratorResult:
    """THE function both triggers call. Evaluates every active policy
    against `record`, decides what that means, and — only if a human is
    actually needed — creates a real Workbench item via the real
    route_to_workbench().

    Every outcome is audit-logged, including an unexpected failure — this
    is what lets routers/dashboard.py compute real "auto executed" /
    "failed" counts despite auto-resolved events having no other row
    anywhere. On failure, this re-raises after logging (the audit entry
    records what nearly happened; the caller still needs to know the
    call itself didn't succeed — routers/orchestrator.py surfaces that as
    a 500, the poller's own try/except in orchestrator_poller.py already
    catches and logs it per-record so one bad record can't stop a batch).
    """
    record_id = str(record.get("id", "")) or None

    try:
        evaluations = await evaluate_policies_for_entity(db, entity_name, record, actor=actor, request=request)
        decision, priority, reasoning, _matched_actions = _decide(entity_name, record, evaluations)

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
    except Exception as exc:  # noqa: BLE001 — logged as a real failure, then re-raised, not swallowed
        await audit.log(
            action=ORCHESTRATOR_DECIDE_ACTION,
            description=f"Orchestrator failed processing a {entity_name} record from '{source}': {exc}",
            actor=actor,
            category="data",
            resource_type=entity_name,
            resource_id=record_id,
            resource_name=str(record.get("title") or record.get("name") or "")[:255] or None,
            metadata={"decision": "failed", "entity_name": entity_name, "source": source},
            success=False,
            error_message=str(exc),
            request=request,
        )
        log.exception("Orchestrator processing failed for %s record %s", entity_name, record_id)
        raise

    await audit.log(
        action=ORCHESTRATOR_DECIDE_ACTION,
        description=f"Orchestrator {decision} for {entity_name} record (priority={priority}): {reasoning}",
        actor=actor,
        category="data",
        resource_type=entity_name,
        resource_id=record_id,
        resource_name=str(record.get("title") or record.get("name") or "")[:255] or None,
        metadata={
            "decision": decision,
            "priority": priority,
            "entity_name": entity_name,
            "source": source,
            "workbench_item_id": workbench_item_id,
            "matched_count": sum(1 for e in evaluations if e.matched),
        },
        success=True,
        request=request,
    )

    return OrchestratorResult(
        entity_name=entity_name,
        policy_evaluations=evaluations,
        matched_count=sum(1 for e in evaluations if e.matched),
        decision=decision,
        priority=priority,
        reasoning=reasoning,
        workbench_item_id=workbench_item_id,
        record_id=record_id,
    )
