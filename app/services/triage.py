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

from sqlalchemy.orm import Session

from ..models.workbench import WorkbenchItem, WorkbenchStatus
from .audit import audit
from .notifications import build_message, send_notification

log = logging.getLogger(__name__)


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
