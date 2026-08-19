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

COLUMN NAMES ARE CONFIGURABLE, NOT HARDCODED — this was wrong in an
earlier version of this file, discovered by actually loading the Round 2
hackathon dataset's real `disruption_notices` table: its primary key
column is `notice_id`, not `id`, and its cursor timestamp is
`received_at`, not `updated_at` (most tables in that dataset only have a
single timestamp column at all — `created_at` or a domain-specific name
like `received_at`, not a true `updated_at`, since these are one-time-
imported synthetic snapshots rather than a live mutating system).
Assuming literal `id`/`updated_at` column names would have made this
module unusable against the actual dataset it's meant to run against.

SUPABASE_SOR_ID_COLUMN and SUPABASE_SOR_TIMESTAMP_COLUMN (both
configurable via env, defaulting to "id"/"updated_at" for a table that
does use those conventional names) tell this module which columns to
treat as the primary key and the incremental-polling cursor. Every row
returned by fetch_new_records() also gets "id" and "updated_at" keys
added (aliasing whatever the configured columns actually are), so
everything downstream (orchestrator_engine.py's dedupe check, Workbench's
entity_id) keeps working unchanged regardless of the source table's real
column names — the ORIGINAL columns are also still present in the dict
under their real names, nothing is renamed or dropped, so Policy Engine
DSL conditions can still reference the table's actual field names (e.g.
`severity`, `notice_type`, `confidence` for the real dataset).
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
_DEFAULT_ID_COLUMN = "id"
_DEFAULT_TIMESTAMP_COLUMN = "updated_at"

# Table/column names come from env vars, not user input — but they're
# still interpolated into raw SQL below (SQLAlchemy's `text()` doesn't
# parameterize identifiers, only values), so this whitelist-style check
# is a defensive belt-and-suspenders against a typo'd or malicious env
# var, not a defense against an untrusted caller.
_VALID_IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


class SystemOfRecordError(Exception):
    """Raised on a real connection/query failure, as opposed to
    SupabaseNotConfiguredError (which means "not set up," not "broken").
    Callers (the poller, health_check.py) distinguish the two."""


def _get_identifier(env_var: str, default: str) -> str:
    value = os.getenv(env_var, default)
    if not _VALID_IDENTIFIER.match(value):
        raise SystemOfRecordError(
            f"{env_var}={value!r} is not a valid SQL identifier — "
            "letters, digits, and underscores only, must not start with a digit."
        )
    return value


def _get_table_name() -> str:
    return _get_identifier("SUPABASE_SOR_TABLE", _DEFAULT_TABLE)


def _get_id_column() -> str:
    return _get_identifier("SUPABASE_SOR_ID_COLUMN", _DEFAULT_ID_COLUMN)


def _get_timestamp_column() -> str:
    return _get_identifier("SUPABASE_SOR_TIMESTAMP_COLUMN", _DEFAULT_TIMESTAMP_COLUMN)


def fetch_new_records(since: Optional[datetime], limit: int = 100) -> list[dict]:
    """
    Returns rows with `<timestamp_column> > since`, oldest first (so the
    poller processes things in the order they actually arrived), as plain
    dicts. `since=None` fetches the oldest `limit` rows overall — used
    the very first time this ever runs, when there's no prior high-water
    mark yet.

    Every returned dict is guaranteed to have "id" and "updated_at" keys
    (aliased from whatever SUPABASE_SOR_ID_COLUMN/SUPABASE_SOR_TIMESTAMP_COLUMN
    are actually configured as) in addition to all the table's real
    columns under their real names — see module docstring for why.

    Deliberately synchronous, not async: this repo's entire database
    layer (core/database.py's Session, used throughout every router) is
    already synchronous SQLAlchemy despite being called from async route
    handlers — this matches that existing, if imperfect, convention
    rather than mixing sync and async DB styles for no real benefit on a
    call that only happens once per poll interval.
    """
    table = _get_table_name()
    id_col = _get_id_column()
    ts_col = _get_timestamp_column()

    # Defensive normalization: a `DateTime(timezone=True)` column reliably
    # round-trips its tzinfo on real Postgres/Supabase, but SQLite (used
    # here to test this module without a live Supabase project — see
    # module docstring) silently drops it, handing back a naive datetime.
    # Comparing a naive `since` against this table's aware timestamps as
    # raw strings breaks the WHERE filter below in exactly the way a
    # naive-vs-aware mismatch always does — assume UTC (this app never
    # stores anything else) rather than let that surface as silently
    # re-fetching already-processed rows forever.
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)

    session_factory = get_supabase_session_factory()  # raises SupabaseNotConfiguredError if unset
    session = session_factory()
    try:
        query = text(
            f"SELECT * FROM {table} "  # noqa: S608 — table/column names validated above
            f"WHERE (:since IS NULL OR {ts_col} > :since) "
            f"ORDER BY {ts_col} ASC LIMIT :limit"
        )
        try:
            result = session.execute(query, {"since": since, "limit": limit})
        except Exception as exc:  # noqa: BLE001 — could be a missing table, bad column, auth failure, etc.
            log.error("Supabase system-of-record query failed (table=%s): %s", table, exc)
            raise SystemOfRecordError(f"Query against '{table}' failed: {exc}") from exc

        rows = []
        for row in result:
            record = dict(row._mapping)
            # Alias, don't rename — every original column stays present
            # under its real name too, so DSL conditions written against
            # the source table's actual field names keep working.
            if id_col in record:
                record["id"] = record[id_col]
            if ts_col in record:
                record["updated_at"] = record[ts_col]
            rows.append(record)

        log.info(
            "Fetched %d new record(s) from Supabase table '%s' (id_col=%s, ts_col=%s, since=%s)",
            len(rows), table, id_col, ts_col, since,
        )
        return rows
    finally:
        session.close()


def latest_updated_at(rows: list[dict]) -> Optional[datetime]:
    """The new high-water mark after processing a batch — the max
    cursor-timestamp seen (reading the "updated_at" alias every row from
    fetch_new_records() carries, regardless of the source column's real
    name), or None if the batch was empty (caller should keep the
    previous mark in that case, not advance it). Using the data's own max
    timestamp rather than `datetime.now()` avoids clock-skew bugs between
    this app's server and Supabase's — the cursor only ever moves forward
    based on what Supabase itself reported.

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


def count_table_rows(table_env_var: str, default_table: str) -> Optional[int]:
    """
    A cheap `SELECT COUNT(*)` against an arbitrary Supabase table, used by
    routers/dashboard.py for the "live counts across the business" tile
    (suppliers, open disruption notices, inventory positions, shipments —
    whatever tables you actually have). Each call is independent and
    fail-soft: returns None (not 0) if Supabase isn't configured or the
    query fails, so a caller can distinguish "we don't know" from
    "genuinely zero rows" — same convention as fetch_new_records/
    latest_updated_at in this module and services/health_check.py's
    UNCONFIGURED vs DOWN distinction.

    Deliberately a plain COUNT(*), not PostgREST's exact-count header
    trick (which is what you'd reach for over Supabase's REST API) —
    this app talks to Supabase as a direct Postgres connection (see
    core/supabase_db.py's module docstring for why), so a straightforward
    SQL count is both simpler and cheaper here than routing through REST
    for something this connection can already do directly.
    """
    table = _get_identifier(table_env_var, default_table)
    try:
        session_factory = get_supabase_session_factory()
    except SupabaseNotConfiguredError:
        return None

    session = session_factory()
    try:
        result = session.execute(text(f"SELECT COUNT(*) FROM {table}"))  # noqa: S608 — table validated above
        return result.scalar()
    except Exception as exc:  # noqa: BLE001 — missing table, auth failure, etc. — fail soft, not fatal
        log.warning("count_table_rows failed for table '%s': %s", table, exc)
        return None
    finally:
        session.close()
