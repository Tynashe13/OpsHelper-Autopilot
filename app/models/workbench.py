# app/models/workbench.py
"""
Workbench model — backs the "1+ real exception routed to Workbench,
resolved by a human" Round 2 requirement.

A WorkbenchItem is one exception that couldn't (or shouldn't) be handled
automatically — e.g. a policy evaluation in services/policy_engine.py
returned a `require_approval` action, or the Orchestrator hit something
it doesn't have a rule for. It sits here until a human resolves it.

Column names follow the same "mirror what the UI will need" philosophy as
models/policy.py, even though (unlike Policy) no frontend page consumes
this yet — frontend/src/app/workbench/page.tsx is currently unrelated
template boilerplate with no API calls, not the real approval UI. See
services/triage.py for how rows get created, and
services/workbench_scheduler.py for how the retry/escalation clock
advances them.
"""
import uuid

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from ..core.database import Base


def _new_id() -> str:
    """Same reasoning as models/policy.py's _new_id: a UUID string that
    Python can generate before the row is ever saved, so callers get a
    real id back immediately without a round-trip to the database."""
    return str(uuid.uuid4())


class WorkbenchStatus:
    """Named constants for the lifecycle of one item — see the class
    docstring on WorkbenchItem below for how they connect. Plain string
    constants, not a DB-enforced enum, so this stays consistent with how
    DataSourceStatus/DataSourceCategory are done in models/data_source.py."""

    PENDING = "pending"          # created, human hasn't acted, notifications may still be retrying
    IN_PROGRESS = "in_progress"  # a human has picked it up but not resolved it yet
    RESOLVED = "resolved"        # a human resolved it — terminal state
    ESCALATED = "escalated"      # retries exhausted, handed to a different target — still needs resolving
    CANCELLED = "cancelled"      # withdrawn/no longer relevant — terminal state


class WorkbenchPriority:
    """Drives both notification urgency (see services/notifications.py)
    and default sort order on the (future) Workbench page."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class WorkbenchItem(Base):
    """
    One row = one exception routed to a human for a decision.

    Retry/escalation spec, as confirmed by the team (see Round 2 handoff
    doc §7): each retry is a MORE urgent re-notification, not a quiet
    repeat — retry_count/max_retries/next_retry_at below are what
    services/workbench_scheduler.py reads every minute to decide whether
    to fire another (louder) notification or, once max_retries is
    exhausted, stop nagging the original target and escalate to a
    different one instead (escalated_to).
    """

    __tablename__ = "workbench_items"

    # String(36) UUID primary key, same reasoning as Policy.id — this row
    # may need to be referenced (e.g. from an audit log's resource_id, or
    # eventually a frontend URL) before it's ever committed.
    id = Column(String(36), primary_key=True, default=_new_id, index=True)

    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False, default="")

    # What kind of record this exception is about (e.g. "recovery_plan",
    # "disruption_notice") and, where known, which specific record —
    # mirrors Policy.entity_name / PolicyEvaluation.entity_id so the same
    # entity_name used in an evaluate() call can be traced through to
    # whatever Workbench item it produced.
    entity_name = Column(String(100), nullable=True, index=True)
    entity_id = Column(String(255), nullable=True, index=True)

    # Where this item came from — "policy_engine", "orchestrator",
    # "manual", etc. Free-text on purpose (like DataSource.system_type):
    # new sources shouldn't require a migration to add.
    source = Column(String(50), nullable=False, default="manual")

    # The actual business data a human needs to make the call — kept as a
    # snapshot (like PolicyEvaluation.input_snapshot) so this item is still
    # fully readable even if the underlying record changes or disappears
    # later.
    payload = Column(JSON, nullable=True)
    reason = Column(Text, nullable=True)  # why this needed a human — e.g. a policy's explanation string

    status = Column(String(20), nullable=False, default=WorkbenchStatus.PENDING, index=True)
    priority = Column(String(20), nullable=False, default=WorkbenchPriority.MEDIUM, index=True)

    # Who/where notifications go. Deliberately a free-text identifier
    # (an email, a Slack user/channel id, whatever) rather than a foreign
    # key — see services/notifications.py, which doesn't yet have a real
    # channel to send through and needs this to stay provider-agnostic.
    notify_target = Column(String(255), nullable=True)
    assigned_to = Column(String(255), nullable=True)  # set once a human actually picks this up

    # --- Retry/escalation clock — read and advanced by
    # services/workbench_scheduler.py, see that file for the actual loop. ---
    retry_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=3)
    retry_interval_minutes = Column(Integer, nullable=False, default=15)
    last_notified_at = Column(DateTime(timezone=True), nullable=True)
    # NULL means "not waiting on a retry" (e.g. already resolved, or
    # escalated and done retrying) — the scheduler's query is just
    # "next_retry_at <= now", so this column doubles as the on/off switch.
    next_retry_at = Column(DateTime(timezone=True), nullable=True, index=True)

    escalated = Column(Boolean, nullable=False, default=False)
    escalated_to = Column(String(255), nullable=True)
    escalated_at = Column(DateTime(timezone=True), nullable=True)

    # --- Resolution — set once, by whichever human closes this out. ---
    resolution = Column(Text, nullable=True)
    resolved_by = Column(String(255), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
