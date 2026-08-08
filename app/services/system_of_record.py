# app/services/system_of_record.py
"""
Read-only access to the Supabase system-of-record table — the actual
external data source the Orchestrator poller (services/orchestrator_poller.py)
reads from, instead of only reacting to manual POST /api/orchestrator/events
calls. This module is the ONLY place in the app that queries Supabase
directly (see core/supabase_db.py's module docstring for why the
connection itself is kept separate) — everything downstream (Orchestrator,
Policy Engine, Workbench) only ever sees plain dicts returned from here,
the same shape as a manually-POSTed event's `record` field.

Table/column assumptions (documented, not hidden): the configured table
(SUPABASE_SOR_TABLE, default "disruptions") is expected to have at least
an `id` and an `updated_at` column — `updated_at` is what makes
incremental polling possible at all (see fetch_new_records' `since`
param). Beyond that, whatever columns exist just pass through as-is into
the record dict; Policy Engine's DSL conditions reference fields by name,
so your policies' `field` values need to match your actual table's
column names.
"""
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text

from ..core.supabase_db import SupabaseNotConfiguredError, get_supabase_session_factory

log = logging.getLogger(__name__)

_DEFAULT_TABLE = "disruptions"

# Table names come from an env var, not user input — but it's still
# interpolated into raw SQL below (SQLAlchemy's `text()` doesn't
# parameterize identifiers, only values), so this whitelist-style check
# is a defensive belt-and-suspenders against a typo'd or malicious env
# var, not a defense against an untrusted caller.
_VALID_IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


class SystemOfRecordError(Exception):
    """Raised on a real connection/query failure, as opposed to
    SupabaseNotConfiguredError (which means "not set up," not "broken").
    Callers (the poller, health_check.py) distinguish the two."""


def _get_table_name() -> str:
    table = os.getenv("SUPABASE_SOR_TABLE", _DEFAULT_TABLE)
    if not _VALID_IDENTIFIER.match(table):
        raise SystemOfRecordError(
            f"SUPABASE_SOR_TABLE={table!r} is not a valid table identifier — "
            "letters, digits, and underscores only, must not start with a digit."
        )
    return table


def fetch_new_records(since: Optional[datetime], limit: int = 100) -> list[dict]:
    """
    Returns rows with `updated_at > since`, oldest first (so the poller
    processes things in the order they actually changed), as plain dicts.
    `since=None` fetches the oldest `limit` rows overall — used the very
    first time this ever runs, when there's no prior high-water mark yet.

    Deliberately synchronous, not async: this repo's entire database
    layer (core/database.py's Session, used throughout every router) is
    already synchronous SQLAlchemy despite being called from async route
    handlers — this matches that existing, if imperfect, convention
    rather than mixing sync and async DB styles for no real benefit on a
    call that only happens once per poll interval.
    """
    table = _get_table_name()
    session_factory = get_supabase_session_factory()  # raises SupabaseNotConfiguredError if unset
    session = session_factory()
    try:
        query = text(
            f"SELECT * FROM {table} "  # noqa: S608 — table validated by _get_table_name() above
            f"WHERE (:since IS NULL OR updated_at > :since) "
            f"ORDER BY updated_at ASC LIMIT :limit"
        )
        try:
            result = session.execute(query, {"since": since, "limit": limit})
        except Exception as exc:  # noqa: BLE001 — could be a missing table, bad column, auth failure, etc.
            log.error("Supabase system-of-record query failed (table=%s): %s", table, exc)
            raise SystemOfRecordError(f"Query against '{table}' failed: {exc}") from exc

        rows = [dict(row._mapping) for row in result]
        log.info("Fetched %d new record(s) from Supabase table '%s' (since=%s)", len(rows), table, since)
        return rows
    finally:
        session.close()


def latest_updated_at(rows: list[dict]) -> Optional[datetime]:
    """The new high-water mark after processing a batch — the max
    `updated_at` seen, or None if the batch was empty (caller should keep
    the previous mark in that case, not advance it). Using the data's own
    max timestamp rather than `datetime.now()` avoids clock-skew bugs
    between this app's server and Supabase's — the cursor only ever moves
    forward based on what Supabase itself reported.

    Normalizes each value to a real datetime first: a genuine Postgres/
    Supabase connection returns proper datetime objects for a `timestamp`
    column, but this is defensive against any driver/DB (e.g. SQLite,
    used to test this module without a live Supabase project) that hands
    back an ISO-format string instead — discovered by actually running
    this against a stand-in DB, not assumed.
    """
    timestamps: list[datetime] = []
    for r in rows:
        raw = r.get("updated_at")
        if raw is None:
            continue
        if isinstance(raw, str):
            try:
                raw = datetime.fromisoformat(raw)
            except ValueError:
                log.warning("Could not parse updated_at value %r as a datetime — skipping for cursor purposes.", raw)
                continue
        timestamps.append(raw)

    if not timestamps:
        return None
    latest = max(timestamps)
    # Supabase/Postgres typically returns timezone-aware datetimes already,
    # but normalize defensively so downstream comparisons (and storing
    # this back on DataSource.last_polled_at, a timezone-aware column)
    # never hit a naive-vs-aware comparison error.
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    return latest
