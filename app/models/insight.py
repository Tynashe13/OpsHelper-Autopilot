# app/models/insight.py
"""
Insight model — backs the "AI Insights" pillar.

Ported from a separate, independently-built lineage during the Round 3
merge (see SESSION_HANDOFF.md §9-10) — this file itself needed no
adaptation, since it only references Insight's own columns, not
DisruptionRun (the other lineage's core entity, which doesn't exist
here). services/insight_engine.py is where the real adaptation happened
(WorkbenchItem instead of DisruptionRun) — see that file's docstring.

Rows here are produced by services/insight_engine.py, computed from real
operational data (PolicyEvaluation + WorkbenchItem) — never seeded, never
hand-written.
"""
import uuid

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, String, Text
from sqlalchemy.sql import func

from ..core.database import Base


def _new_id() -> str:
    return str(uuid.uuid4())


class Insight(Base):
    __tablename__ = "insights"

    id = Column(String(36), primary_key=True, default=_new_id, index=True)

    # A stable, deterministic string the insight engine computes each run
    # (e.g. "policy_recurrence:<policy_id>") so regenerating insights
    # UPDATES the same row instead of creating duplicates every time the
    # Insights page loads — see insight_engine.py's upsert logic. Not
    # exposed to the frontend; internal bookkeeping only.
    source_key = Column(String(255), nullable=False, unique=True, index=True)

    type = Column(String(30), nullable=False)          # 'pattern' | 'anomaly' | 'recommendation' | 'trend' | 'alert'
    severity = Column(String(20), nullable=False)       # 'critical' | 'high' | 'warning' | 'medium' | 'low' | 'info'
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    data = Column(JSON, nullable=True)                  # the concrete numbers backing the insight
    suggested_action = Column(Text, nullable=True)
    action_type = Column(String(50), nullable=True)
    confidence = Column(Float, nullable=True)

    is_dismissed = Column(Boolean, nullable=False, default=False)
    is_actioned = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
