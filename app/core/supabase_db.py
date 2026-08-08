# app/core/supabase_db.py
"""
Second SQLAlchemy engine — points at Supabase (the "Orders & Inventory
Store" / system-of-record DataSource), completely separate from the
app's own database in core/database.py.

Kept deliberately separate rather than repointing DATABASE_URL: this
app's own tables (policies, workbench_items, audit_logs, ...) must never
live in the same database/migration history as Supabase's tables, which
this app only ever reads from (see services/system_of_record.py's module
docstring) and doesn't own or migrate. Two engines, two session
factories, two completely independent connection lifecycles.

Lazily constructed, not built at import time: SUPABASE_DB_URL is optional
(the Round 3 plan's system-of-record connection is one integration among
several, not a hard dependency for the app to start), so importing this
module must never crash the app just because Supabase isn't configured
yet. Every caller (services/system_of_record.py, services/health_check.py)
is expected to catch SupabaseNotConfiguredError and degrade gracefully —
the poller no-ops, the Data Manager row shows "unconfigured" — never a
crash at startup or on a request.
"""
import logging
import os
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

log = logging.getLogger(__name__)


class SupabaseNotConfiguredError(Exception):
    """Raised when SUPABASE_DB_URL isn't set. Distinct from a real
    connection failure (bad credentials, network down, table missing) —
    callers branch on this specifically to show "unconfigured" instead of
    "down"."""


@lru_cache(maxsize=1)
def _get_engine():
    """Built once, on first actual use, and cached — same reasoning as
    security.py's get_jwks(): nothing calls this at import time, so a
    missing SUPABASE_DB_URL only ever surfaces when something genuinely
    tries to use the connection, not when the app boots."""
    url = os.getenv("SUPABASE_DB_URL")
    if not url:
        raise SupabaseNotConfiguredError("SUPABASE_DB_URL not set")
    log.info("Supabase engine initialized")
    return create_engine(url, pool_pre_ping=True)


@lru_cache(maxsize=1)
def _get_session_factory():
    return sessionmaker(autocommit=False, autoflush=False, bind=_get_engine())


def get_supabase_session_factory():
    """The one function callers actually use — returns a session factory
    (call it to get a session, same shape as core/database.py's
    SessionLocal). Raises SupabaseNotConfiguredError immediately if
    SUPABASE_DB_URL isn't set, rather than returning something that fails
    later and more confusingly on first query."""
    return _get_session_factory()


def reset_supabase_engine_cache() -> None:
    """Test-only escape hatch: lru_cache means _get_engine()/
    _get_session_factory() only ever read SUPABASE_DB_URL once per
    process. A test that sets/unsets the env var between cases needs to
    clear these caches first, or it'll keep seeing whatever was cached
    from an earlier test."""
    _get_engine.cache_clear()
    _get_session_factory.cache_clear()
