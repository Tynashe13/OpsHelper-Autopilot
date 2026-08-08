# app/services/triage.py
"""
Triage service — the actual "route an exception to Workbench" step.

This is the function that satisfies the Round 2 requirement "1+ real
exception routed to Workbench, resolved by a human": something upstream
(a policy evaluation that came back matched with a `require_approval`
action, or the Orchestrator hitting a case it has no rule for) calls
route_to_workbench() with the details, and this module is responsible for
persisting the item, logging it, and firing the first notification.

Deliberately thin: this is NOT where "who should this go to" business
logic gets decided (that's the caller's job — a policy's `actions` list,
or the Orchestrator step, already knows who/what triggered this). This
module only knows how to take that decision and turn it into a real,
tracked, notified WorkbenchItem — the same separation of concerns
services/policy_engine.py uses between "which policies apply" (its job)
and "what to actually do about it" (the caller's job).
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Request
from sqlalchemy.orm import Session

from ..models.workbench import WorkbenchItem, WorkbenchStatus
from ..schemas.policy import PolicyEvaluationResult
from .audit import audit
from .notifications import build_message, send_notification

log = logging.getLogger(__name__)

# Action `type` strings from a matched policy's PolicyAction that mean
# "just do it, no human needed" — see the three seed policies in
# alembic/versions/e5f6g7h8i9j0_add_policies_tables.py for real examples
# ("auto_execute"). auto_resolve/auto_approve are included so an
# authored or LLM-suggested policy can use whichever verb reads more
# naturally without triage_evaluation() below needing a special case.
_AUTO_ACTION_TYPES = {"auto_execute", "auto_resolve", "auto_approve"}

# Action types that mean "skip straight to escalation" (as opposed to a
# regular human-review Workbench item) — reserved for policies that are
# explicit about severity, e.g. {"type": "escalate", "value": "commander"}.
_ESCALATE_ACTION_TYPES = {"escalate"}


async def triage_evaluation(
    db: Session,
    *,
    entity_name: str,
    record: dict,
    evaluations: list[PolicyEvaluationResult],
    source: Optional[str] = None,
    actor: Optional[dict] = None,
    request: Optional[Request] = None,
) -> tuple[str, str, Optional[WorkbenchItem]]:
    """
    THE decision function — turns a batch of Policy Engine evaluations
    (services/policy_engine.py's evaluate_policies_for_entity output) into
    one of three outcomes, and does whatever that outcome requires:

      - "auto_resolve": every matched policy's action is a pure
        auto-type (_AUTO_ACTION_TYPES) — nothing needs a human. No
        Workbench item is created.
      - "escalate": a matched policy's action type is "escalate" —
        immediately routed to Workbench at "critical" priority.
      - "route_to_workbench": either a matched policy's action calls for
        human review (anything not in _AUTO_ACTION_TYPES or
        _ESCALATE_ACTION_TYPES — covers "require_approval" and whatever
        a natural_language policy's LLM judgment invents, e.g. a
        contract-clause conflict flag), OR no policy matched at all.

    That last case — nothing matched — is deliberate, not an oversight:
    this is what the hackathon brief's "Golden Rule" means by "a system
    that gracefully handles what it can't do" — an event the Orchestrator
    has no rule for still needs a human to look at it, not to be silently
    dropped. Every call through this function therefore ends in either a
    logged auto-resolution or a real, visible Workbench item — never both,
    never neither.

    Called from services/orchestrator_engine.process_event(), which is in
    turn called from BOTH routers/orchestrator.py (manual/API trigger)
    and services/orchestrator_poller.py (the Supabase poller) — so this
    is the one place "what happens to an event" is ever decided.
    """
    matched = [e for e in evaluations if e.matched]

    if not matched:
        item = await route_to_workbench(
            db,
            title=f"Unhandled {entity_name} — no policy matched",
            description=(
                "No active policy matched this record. Routed for human "
                "review per the Golden Rule: grace over silent failure."
            ),
            entity_name=entity_name,
            entity_id=str(record.get("id", "")) or None,
            source=source or "orchestrator",
            payload=record,
            reason="No policy matched",
            priority="medium",
            actor=actor,
        )
        return (
            "route_to_workbench",
            "No policy matched this record; routed for human review.",
            item,
        )

    escalate_hits: list[tuple[PolicyEvaluationResult, object]] = []
    human_hits: list[tuple[PolicyEvaluationResult, object]] = []
    auto_hits: list[tuple[PolicyEvaluationResult, object]] = []

    for evaluation in matched:
        for action in evaluation.actions:
            action_type = (action.type or "").lower()
            if action_type in _ESCALATE_ACTION_TYPES:
                escalate_hits.append((evaluation, action))
            elif action_type in _AUTO_ACTION_TYPES:
                auto_hits.append((evaluation, action))
            else:
                # Includes "require_approval" and any LLM-invented action
                # type from a natural_language policy — safety-first
                # default: anything we don't explicitly recognize as
                # "auto" goes to a human rather than being silently
                # executed on an unrecognized instruction.
                human_hits.append((evaluation, action))

    # Safety ordering: if ANY matched policy calls for escalation or
    # human review, that wins over any other matched policy's
    # auto-execute — one policy saying "this is fine" never overrides
    # another policy saying "a human needs to see this".
    if escalate_hits:
        evaluation, action = escalate_hits[0]
        item = await route_to_workbench(
            db,
            title=f"Escalation required: {evaluation.policy_name}",
            description=evaluation.explanation,
            entity_name=entity_name,
            entity_id=str(record.get("id", "")) or None,
            source=source or "orchestrator",
            payload=record,
            reason=f"Policy '{evaluation.policy_name}' called for escalation",
            priority="critical",
            notify_target=str(action.value) if action.value else None,
            actor=actor,
        )
        return (
            "escalate",
            f"Policy '{evaluation.policy_name}' called for escalation.",
            item,
        )

    if human_hits:
        evaluation, action = human_hits[0]
        item = await route_to_workbench(
            db,
            title=f"Approval needed: {evaluation.policy_name}",
            description=evaluation.explanation,
            entity_name=entity_name,
            entity_id=str(record.get("id", "")) or None,
            source=source or "orchestrator",
            payload=record,
            reason=f"Policy '{evaluation.policy_name}' requires human review ({action.type})",
            priority="high",
            notify_target=str(action.value) if action.value else None,
            actor=actor,
        )
        return (
            "route_to_workbench",
            f"Policy '{evaluation.policy_name}' requires human review.",
            item,
        )

    # Every matched policy's action was a pure auto-type — nothing needs
    # a human. Still audit-logged (policy_engine.py already logged each
    # individual policy evaluation; this logs the overall triage outcome)
    # so an auto-resolution leaves the same kind of trail a Workbench
    # resolution does, just without a Workbench item.
    policy_names = ", ".join(evaluation.policy_name for evaluation in matched)
    reason = f"Auto-resolved by: {policy_names}"
    await audit.log(
        action="orchestrator.auto_resolve",
        description=f"{entity_name} record auto-resolved: {reason}",
        actor=actor,
        category="data",
        resource_type="orchestrator_event",
        resource_id=str(record.get("id", "")) or None,
        resource_name=entity_name,
        metadata={"entity_name": entity_name, "matched_policies": policy_names, "source": source},
        request=request,
    )
    return "auto_resolve", reason, None


async def route_to_workbench(
    db: Session,
    *,
    title: str,
    description: str = "",
    entity_name: str | None = None,
    entity_id: str | None = None,
    source: str = "manual",
    payload: dict | None = None,
    reason: str | None = None,
    priority: str = "medium",
    notify_target: str | None = None,
    max_retries: int = 3,
    retry_interval_minutes: int = 15,
    actor: dict | None = None,
) -> WorkbenchItem:
    """
    Creates a new WorkbenchItem, persists it, audit-logs the routing
    decision, and sends the first notification — this is the whole
    "exception routed to Workbench" event, start to finish, in one call.

    actor is Optional and usually None: this is most often called from a
    system process (the Policy Engine, an Orchestrator-integration
    endpoint), not directly by a logged-in human — same pattern as
    services/health_check.py's callers.
    """
    item = WorkbenchItem(
        title=title,
        description=description,
        entity_name=entity_name,
        entity_id=entity_id,
        source=source,
        payload=payload,
        reason=reason,
        status=WorkbenchStatus.PENDING,
        priority=priority,
        notify_target=notify_target,
        max_retries=max_retries,
        retry_interval_minutes=retry_interval_minutes,
        retry_count=0,
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    await audit.log(
        action="workbench.route",
        description=f"Routed exception '{title}' to Workbench (source: {source})",
        actor=actor,
        category="data",
        resource_type="workbench_item",
        resource_id=item.id,
        resource_name=item.title,
        metadata={
            "entity_name": entity_name,
            "entity_id": entity_id,
            "source": source,
            "priority": priority,
            "reason": reason,
        },
    )

    # Fire the first notification immediately (attempt 0 — see
    # notifications.py's urgency ladder) rather than waiting for the
    # scheduler's next tick, so a human finds out right away instead of
    # up to a minute later. Best-effort: a failed notification shouldn't
    # stop the item from existing and being visible/resolvable via the API.
    now = datetime.now(timezone.utc)
    if notify_target:
        message = build_message(item, attempt=0)
        try:
            await send_notification(item, message, notify_target)
        except Exception as exc:  # noqa: BLE001 — never let a notification failure block triage
            log.error("Initial notification failed for Workbench item %s: %s", item.id, exc)
        item.last_notified_at = now

    # Arms the retry clock — services/workbench_scheduler.py's query is
    # "next_retry_at <= now", so this is what makes the item eligible for
    # its first retry once retry_interval_minutes has passed. If
    # max_retries is 0, leave next_retry_at unset: an item with no
    # retries configured shouldn't get a retry loop.
    if max_retries > 0:
        item.next_retry_at = now + timedelta(minutes=retry_interval_minutes)
    db.commit()
    db.refresh(item)

    return item
