# app/core/supabase_db.py
"""
A second, completely separate SQLAlchemy engine/session factory for the
Supabase system-of-record connection — deliberately NOT sharing anything
with core/database.py's `engine`/`SessionLocal`/`Base`.

Why separate rather than reusing core/database.py: this app's own tables
(policies, workbench_items, audit_logs, ...) and Supabase's tables
(whatever the customer's `disruptions`/orders/inventory schema looks
like) must never end up in the same migration history or the same
`Base.metadata` — mixing them would mean `alembic upgrade head` could
try to manage tables it doesn't own, and `Base.metadata.create_all()`
could try to create tables that already exist in a project we don't
control the schema of. Keeping the connection itself separate is what
enforces that boundary, not just a convention to remember.

This module is read-only in intent (see services/system_of_record.py,
the only caller) — nothing here ever runs an INSERT/UPDATE/DELETE.
"""
import logging
import os
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

log = logging.getLogger(__name__)

_engine = None
_SessionFactory: Optional[sessionmaker] = None


class SupabaseNotConfiguredError(Exception):
    """Raised when SUPABASE_DB_URL isn't set. Distinct from a real
    connection failure — callers (services/system_of_record.py,
    services/health_check.py, services/orchestrator_poller.py) treat
    'not configured' as DataSourceStatus.UNCONFIGURED, not DOWN."""


def _build_session_factory() -> sessionmaker:
    db_url = os.getenv("SUPABASE_DB_URL")
    if not db_url:
        raise SupabaseNotConfiguredError("SUPABASE_DB_URL not set")

    global _engine
    if _engine is None:
        # pool_pre_ping=True: checks a pooled connection is still alive
        # before handing it out, rather than failing mid-query — matters
        # more here than for core/database.py's engine, since this one
        # talks to a remote hosted Postgres (Supabase) that can drop idle
        # connections, not a same-network app database.
        _engine = create_engine(db_url, pool_pre_ping=True)
        log.info("Supabase engine initialized")

    return sessionmaker(autocommit=False, autoflush=False, bind=_engine)


def get_supabase_session_factory() -> sessionmaker:
    """Lazily builds (once) and returns the sessionmaker. Raises
    SupabaseNotConfiguredError if SUPABASE_DB_URL isn't set — every
    caller is expected to catch that specifically and treat it as
    'not set up yet', not a crash."""
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = _build_session_factory()
    return _SessionFactory


def get_supabase_session() -> Session:
    """Convenience: one-shot session for callers that don't need the
    factory itself (e.g. a quick health-check connection test). Caller
    is responsible for closing it."""
    return get_supabase_session_factory()()


def is_configured() -> bool:
    """Cheap check used by services/orchestrator_poller.py's start_poller()
    to decide whether to schedule the poll job at all, without raising."""
    return bool(os.getenv("SUPABASE_DB_URL"))
