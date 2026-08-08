# app/services/notifications.py
"""
Notification service for Workbench items.

Deliberately provider-agnostic: as of this repo, no Slack (or any other)
integration code exists anywhere yet (see Round 2 handoff doc §7/§8 —
"currently zero Slack code exists anywhere in the repo, despite the
design being finalized"). Rather than guess at Slack's API shape and get
it wrong, this module gives services/triage.py and
services/workbench_scheduler.py a single, real call site
(`send_notification`) to depend on. Every notification is always
audit-logged (so "notified commander about X" is provable even before a
real channel is wired up); the actual delivery step below is the one
function to replace once a Slack DataSource exists.

Retry/escalation wording, as confirmed by the team (handoff doc §7): each
retry must read as MORE urgent than the last, not a quiet repeat. That
urgency ladder lives here (`_URGENCY_PREFIXES`) so both the first
notification and every retry after it go through the exact same wording
logic — there's no separate "first message" template to drift out of
sync with the retry templates.
"""
import logging
from typing import TYPE_CHECKING

from .audit import audit

if TYPE_CHECKING:  # avoid a circular import — this module is only used for type hints
    from ..models.workbench import WorkbenchItem

log = logging.getLogger(__name__)


# Index = retry attempt number (0 = first notification). Capped at the
# last entry for anything beyond what's listed — see _urgency_prefix().
_URGENCY_PREFIXES = [
    "New item",
    "Reminder",
    "Follow-up — please respond",
    "URGENT — still awaiting response",
]


def _urgency_prefix(attempt: int) -> str:
    """attempt 0 = the very first notification, 1 = first retry, etc.
    Anything past the last defined level just repeats the most urgent
    wording — escalation (a different target entirely) is what actually
    happens next, not an ever-longer string of exclamation points."""
    index = min(attempt, len(_URGENCY_PREFIXES) - 1)
    return _URGENCY_PREFIXES[index]


def build_message(item: "WorkbenchItem", attempt: int) -> str:
    """Builds the human-readable notification text for one attempt.
    Pulled out as its own function (rather than inlined into
    send_notification) so services/workbench_scheduler.py — or a test —
    can preview exactly what would be sent without triggering delivery."""
    prefix = _urgency_prefix(attempt)
    return (
        f"[{prefix}] Workbench item '{item.title}' "
        f"(priority: {item.priority}) needs a decision. {item.reason or item.description}"
    )


def build_escalation_message(item: "WorkbenchItem") -> str:
    """Wording for the one-time message sent to the NEW target once
    retries are exhausted — distinct from build_message's retry ladder,
    since this is a handoff to someone who hasn't seen this item before,
    not another nudge to someone who's already been asked."""
    return (
        f"[ESCALATED] Workbench item '{item.title}' (priority: {item.priority}) "
        f"was not actioned after {item.max_retries} attempts and has been "
        f"escalated to you. {item.reason or item.description}"
    )


async def send_notification(item: "WorkbenchItem", message: str, target: str) -> bool:
    """
    The single real delivery point. Right now this only logs — both to
    the application logger (so it's visible in a live demo's terminal
    output) and to the audit trail (so every notification, successful or
    not, is provable after the fact, matching the "every evaluation
    logged" pattern the Policy Engine and Data Manager already follow).

    Returns True/False rather than raising, on purpose: a failed
    notification should never be allowed to crash triage or the
    scheduler's background loop — see the callers in triage.py and
    workbench_scheduler.py, which both treat this as best-effort.

    TO WIRE UP REAL SLACK DELIVERY: replace the body of this function
    with an actual call to the Slack DataSource's endpoint/token (once
    that DataSource exists — see Data Manager). Keep the signature and
    the audit.log() call below exactly as-is so triage.py and
    workbench_scheduler.py need no changes.
    """
    if not target:
        log.warning("No notify_target set for Workbench item %s — skipping delivery", item.id)
        delivered = False
    else:
        # Placeholder delivery: this is the line a real Slack (or email,
        # or Teams) integration replaces with an actual API call.
        log.info("[notification -> %s] %s", target, message)
        delivered = True

    await audit.log(
        action="workbench.notify",
        description=f"Notification for Workbench item '{item.title}': {message}",
        category="data",
        severity="info" if delivered else "warning",
        resource_type="workbench_item",
        resource_id=item.id,
        resource_name=item.title,
        metadata={"target": target, "delivered": delivered, "retry_count": item.retry_count},
        success=delivered,
    )
    return delivered
