# app/models/data_source.py
"""
Data Source Model — backs the "Data Manager" pillar.

A DataSource is any external system the AI Employee talks to: a channel
(email/Teams inbox that disruption notices arrive on), a system of record
(Airtable/Supabase holding orders + inventory), a human loop (Slack/Teams
approvals), or the Auto orchestration platform itself. The Data Manager
page shows every row here with live health status.
"""
# SQLAlchemy is the ORM (Object-Relational Mapper) this template uses — it
# lets us describe a database table as a Python class instead of writing raw
# SQL. `Column` describes one column; `func` gives us database-side helpers
# like `now()` so timestamps are set by Postgres itself, not by Python.
from sqlalchemy import Column, DateTime, Float, Integer, String, Text
from sqlalchemy.sql import func

# `Base` is the shared parent class every model in this app inherits from.
# SQLAlchemy uses it to keep track of every table so Alembic (the migration
# tool) can compare "what Python says the tables should look like" against
# "what the database actually has."
from ..core.database import Base


class DataSourceCategory:
    """
    Plain string constants, not a database table — this class exists purely
    so the rest of the codebase can write `DataSourceCategory.CHANNEL`
    instead of the bare string "channel" (autocomplete-friendly, and a typo
    like "channle" becomes an obvious code error instead of a silent bug).

    Matches the Round 2 integration requirement: at least one channel
    and one system of record, plus the human loop and the Auto platform.
    """

    CHANNEL = "channel"                # e.g. email/Teams disruption notices
    SYSTEM_OF_RECORD = "system_of_record"  # e.g. Airtable/Supabase orders+inventory
    HUMAN_LOOP = "human_loop"          # e.g. Slack/Teams Workbench approvals
    AGENT_PLATFORM = "agent_platform"  # Auto (Orchestrator + Operators)


class DataSourceStatus:
    """Same idea as DataSourceCategory above — named constants for the
    four states a connection can be in, used by both this model and by
    app/services/health_check.py so the two files always agree on spelling."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"
    UNCONFIGURED = "unconfigured"


class DataSource(Base):
    """
    One row = one connected system (an Outlook inbox, a Supabase database,
    a Slack workspace, the Auto platform itself). This class becomes a real
    Postgres table called "data_sources" once the matching Alembic migration
    runs — see alembic/versions/d4e5f6g7h8i9_add_data_sources_table.py.
    """

    # __tablename__ tells SQLAlchemy what to call this table in Postgres.
    __tablename__ = "data_sources"

    # Every table needs a primary key — a column that uniquely identifies
    # each row. `index=True` tells Postgres to build a fast lookup structure
    # for this column, since we'll query by id constantly (GET /sources/5).
    id = Column(Integer, primary_key=True, index=True)

    name = Column(String(255), nullable=False)  # nullable=False = required field
    # `index=True` here too, because we filter by category a lot (the Data
    # Manager page groups systems by category on every page load).
    category = Column(String(50), nullable=False, index=True)
    system_type = Column(String(100), nullable=False)  # "airtable", "supabase", "slack", "email", "auto"
    description = Column(Text, nullable=True)  # nullable=True = optional field

    # Connection details (non-secret only — never store API keys/tokens here,
    # pull those from environment variables via system_type at check-time).
    # This is a deliberate security boundary: this table might be visible
    # in an admin UI or exported one day, so it should never be able to leak
    # a credential even by accident.
    endpoint_url = Column(String(500), nullable=True)
    config = Column(Text, nullable=True)  # JSON string for extra, non-secret metadata

    # --- Live health fields, updated every time a health check runs ---
    status = Column(String(20), nullable=False, default=DataSourceStatus.UNCONFIGURED)
    last_checked_at = Column(DateTime(timezone=True), nullable=True)
    last_success_at = Column(DateTime(timezone=True), nullable=True)  # last time it was actually healthy
    latency_ms = Column(Float, nullable=True)
    error_message = Column(Text, nullable=True)  # human-readable reason if status isn't "healthy"

    # High-water mark for the Orchestrator poller (services/orchestrator_poller.py)
    # — only meaningful for the system_of_record row(s) it actually polls.
    # Stores the max `updated_at` seen in the last successfully-processed
    # batch from Supabase (see services/system_of_record.latest_updated_at),
    # NOT this app's own clock — see that function's docstring for why.
    # Null means "never successfully polled yet" -> the poller fetches the
    # oldest rows first rather than assuming everything is new.
    last_polled_at = Column(DateTime(timezone=True), nullable=True)

    # `server_default=func.now()` means Postgres itself fills this in when a
    # row is inserted (more reliable than setting it from Python, since it
    # can't be skipped by accident). `onupdate=func.now()` on updated_at
    # means Postgres refreshes it automatically on every UPDATE, no extra
    # code needed anywhere else in the app.
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
