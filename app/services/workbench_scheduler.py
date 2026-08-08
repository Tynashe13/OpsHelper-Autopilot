# app/services/workbench_scheduler.py
"""
Background retry/escalation clock for Workbench items.

Design decision, as confirmed by the team (Round 2 handoff doc §7):
APScheduler, checking every 1 minute, chosen over frontend-polling or a
full task queue (Celery/Redis) — this needs to actually fire in the
background regardless of whether anyone has the Workbench page open,
especially for a live demo moment, and APScheduler is a small dependency
that needs no separate infrastructure.

Spec, also confirmed by the team: each retry is a MORE urgent
re-notification (see notifications.py's urgency ladder), not a quiet
repeat. Once max_retries is exhausted, stop nagging the original target
the same way and escalate to a different target instead.

Call start_scheduler() once, from main.py's startup, and stop_scheduler()
from shutdown — see the two lines added to main.py alongside this file.
"""
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import or_

from ..core.database import SessionLocal
from ..models.workbench import WorkbenchItem, WorkbenchStatus
from .audit import audit
from .notifications import build_escalation_message, build_message, send_notification

log = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None

# Default escalation target used when a WorkbenchItem doesn't specify its
# own via a future "escalation_target" field — kept as one constant so a
# real config value (e.g. a second Slack channel) can replace it in one
# place. Deliberately not read from an env var yet, since no escalation
# channel exists in .env.example (see the Data Manager / Slack gap in the
# Round 2 handoff doc §8) — replace this once that channel is real.
_DEFAULT_ESCALATION_TARGET = "commander-escalations"


async def _check_due_items() -> None:
    """
    The actual job body, run every minute. Opens its own DB session
    (SessionLocal directly, not the get_db FastAPI dependency, since this
    runs outside any request) and processes every item whose retry clock
    has come due.
    """
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        due_items = (
            db.query(WorkbenchItem)
            .filter(
                WorkbenchItem.next_retry_at.isnot(None),
                WorkbenchItem.next_retry_at <= now,
                # Only items still actually waiting on a human — a
                # resolved/cancelled item might still have a stale
                # next_retry_at if it was closed out between ticks, and
                # should never generate a nag after the fact.
                or_(
                    WorkbenchItem.status == WorkbenchStatus.PENDING,
                    WorkbenchItem.status == WorkbenchStatus.IN_PROGRESS,
                ),
            )
            .all()
        )

        for item in due_items:
            try:
                await _process_due_item(db, item, now)
            except Exception as exc:  # noqa: BLE001 — one bad item must not stop the whole batch
                log.error("Failed processing Workbench item %s in scheduler: %s", item.id, exc)
        if due_items:
            db.commit()
    finally:
        db.close()


async def _process_due_item(db, item: WorkbenchItem, now: datetime) -> None:
    """One item's worth of the retry-or-escalate decision. Split out from
    _check_due_items so the per-item logic is testable/readable on its
    own, and so one item's exception (caught by the caller) can't corrupt
    another item's processing in the same batch."""
    if item.retry_count < item.max_retries:
        # Still within budget: re-notify the SAME target, one level more
        # urgent than last time (attempt = retry_count + 1, since
        # attempt 0 was the initial notification sent by triage.py).
        item.retry_count += 1
        message = build_message(item, attempt=item.retry_count)
        if item.notify_target:
            await send_notification(item, message, item.notify_target)
        item.last_notified_at = now
        item.next_retry_at = now + timedelta(minutes=item.retry_interval_minutes)
    else:
        # Budget exhausted: stop nagging the original target, escalate to
        # a different one instead — this is a one-time handoff, not
        # another entry in the same retry ladder, so it uses
        # build_escalation_message rather than build_message.
        escalation_target = _DEFAULT_ESCALATION_TARGET
        message = build_escalation_message(item)
        await send_notification(item, message, escalation_target)

        item.escalated = True
        item.escalated_to = escalation_target
        item.escalated_at = now
        item.status = WorkbenchStatus.ESCALATED
        # Escalation is a one-time handoff, not another retry cycle —
        # clearing next_retry_at stops this item from matching the
        # scheduler's query again. Whoever picks it up now resolves it
        # directly via POST /workbench/{id}/resolve.
        item.next_retry_at = None

        await audit.log(
            action="workbench.escalate",
            description=f"Workbench item '{item.title}' escalated to {escalation_target} "
            f"after {item.retry_count} unanswered notifications",
            category="data",
            severity="warning",
            resource_type="workbench_item",
            resource_id=item.id,
            resource_name=item.title,
            metadata={"retry_count": item.retry_count, "escalated_to": escalation_target},
        )

    db.add(item)


def start_scheduler() -> None:
    """Call once, from main.py's startup event. Safe to call more than
    once (e.g. in a test) — a second call is a no-op rather than starting
    a duplicate job."""
    global _scheduler
    if _scheduler is not None:
        log.info("Workbench scheduler already running — skipping duplicate start")
        return

    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        _check_due_items,
        "interval",
        minutes=1,
        id="workbench_retry_escalation",
        replace_existing=True,
    )
    _scheduler.start()
    log.info("Workbench retry/escalation scheduler started (checking every 1 minute)")


def stop_scheduler() -> None:
    """Call once, from main.py's shutdown event."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("Workbench retry/escalation scheduler stopped")
