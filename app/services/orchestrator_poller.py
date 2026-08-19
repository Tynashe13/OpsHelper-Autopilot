# app/services/orchestrator_poller.py
"""
Scheduled poller that reads new rows from the Supabase system-of-record
table and feeds each one through the Orchestrator automatically — the
"live trigger" that doesn't depend on someone calling
POST /api/orchestrator/events by hand.

Same in-process APScheduler pattern as services/workbench_scheduler.py:
a BackgroundScheduler job on a short interval
(ORCHESTRATOR_POLL_INTERVAL_SECONDS, default 30s), started/stopped via
app/main.py's lifespan. Read-only against Supabase (see
services/system_of_record.py) — every row this finds gets fed through
services/orchestrator_engine.process_event(), the exact same function
POST /api/orchestrator/events calls, so a row landing in Supabase and a
manually-POSTed event produce identical downstream behavior.

If SUPABASE_DB_URL isn't set, this no-ops on every tick (logs once, then
stays quiet) rather than crashing the scheduler or the app — matches
core/supabase_db.py's "optional integration" design.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from ..core.database import SessionLocal
from ..core.supabase_db import SupabaseNotConfiguredError
from ..models.data_source import DataSource, DataSourceCategory, DataSourceStatus
from .orchestrator_engine import process_event
from .system_of_record import SystemOfRecordError, fetch_new_records, latest_updated_at

log = logging.getLogger(__name__)

_DEFAULT_INTERVAL_SECONDS = 30
# Matches the entity_name every seed policy actually uses (see
# alembic/versions/e5f6g7h8i9j0_add_policies_tables.py's INSERTs) — a
# mismatch here means every Supabase-sourced record matches zero seed
# policies out of the box, silently defaulting through the "no policy
# matched" path in orchestrator_engine._decide() instead of actually
# exercising the demo policies. Override via SUPABASE_SOR_ENTITY_NAME if
# your own policies use a different entity_name.
_DEFAULT_ENTITY_NAME = "recovery_plan"
_DEFAULT_LIMIT_PER_TICK = 100

_scheduler: BackgroundScheduler | None = None
_warned_unconfigured = False  # log the "not configured" state once, not every tick


def _get_poll_interval() -> int:
    try:
        return int(os.getenv("ORCHESTRATOR_POLL_INTERVAL_SECONDS", str(_DEFAULT_INTERVAL_SECONDS)))
    except ValueError:
        return _DEFAULT_INTERVAL_SECONDS


def _get_entity_name() -> str:
    """Which Policy Engine entity_name incoming Supabase rows are
    evaluated as. Defaults to "recovery_plan" to match the seed
    policies' actual entity_name (see alembic/versions/
    e5f6g7h8i9j0_add_policies_tables.py) — override via env if your
    Supabase table represents something else, or if your policies use a
    different entity_name."""
    return os.getenv("SUPABASE_SOR_ENTITY_NAME", _DEFAULT_ENTITY_NAME)


def _get_source_row(db) -> DataSource | None:
    """The DataSource row this poller reads/writes last_polled_at on.
    Looks for the system_of_record-category row with system_type
    "supabase" specifically — if your seed data uses a different
    system_type string for this row, update the filter here (or the seed
    data) so the two agree. Intentionally not a silent
    first-row-of-category fallback: guessing the wrong row would silently
    corrupt a different integration's health status and high-water mark."""
    return (
        db.query(DataSource)
        .filter(DataSource.category == DataSourceCategory.SYSTEM_OF_RECORD)
        .filter(DataSource.system_type == "supabase")
        .first()
    )


def poll_once() -> None:
    """
    The actual tick, run every _get_poll_interval() seconds by the
    APScheduler job below. Pulled out as its own top-level, synchronous
    function for the same reason as workbench_scheduler.py's
    check_overdue_items(): callable directly and synchronously from a
    verification script — insert a row in Supabase (or a local stand-in
    DB), call this once, assert a WorkbenchItem/auto-resolution resulted
    — no running scheduler required.
    """
    global _warned_unconfigured
    db = SessionLocal()
    try:
        source_row = _get_source_row(db)
        since = source_row.last_polled_at if source_row else None

        try:
            rows = fetch_new_records(since, limit=_DEFAULT_LIMIT_PER_TICK)
        except SupabaseNotConfiguredError as exc:
            if not _warned_unconfigured:
                log.info("Orchestrator poller idle: %s", exc)
                _warned_unconfigured = True
            return
        except SystemOfRecordError as exc:
            log.error("Orchestrator poller: system-of-record fetch failed: %s", exc)
            if source_row is not None:
                source_row.status = DataSourceStatus.DOWN
                source_row.error_message = str(exc)
                source_row.last_checked_at = datetime.now(timezone.utc)
                db.commit()
            return

        _warned_unconfigured = False
        if not rows:
            return

        entity_name = _get_entity_name()
        log.info("Orchestrator poller: processing %d new record(s) as '%s'", len(rows), entity_name)

        for record in rows:
            try:
                asyncio.run(
                    process_event(
                        db,
                        entity_name=entity_name,
                        record=record,
                        source="supabase_poller",
                        actor=None,
                    )
                )
            except Exception:  # noqa: BLE001 — one bad record must not stop the batch
                log.exception(
                    "Orchestrator poller: processing record id=%s failed", record.get("id")
                )

        new_mark = latest_updated_at(rows)
        if new_mark and source_row is not None:
            source_row.last_polled_at = new_mark
            source_row.status = DataSourceStatus.HEALTHY
            source_row.last_checked_at = datetime.now(timezone.utc)
            source_row.last_success_at = datetime.now(timezone.utc)
            db.commit()
    except Exception:  # noqa: BLE001 — a poller tick must never crash the process
        log.exception("Orchestrator poller tick failed")
    finally:
        db.close()


def start_poller() -> None:
    """Called from app/main.py's lifespan on startup."""
    global _scheduler
    if _scheduler is not None:
        log.warning("Orchestrator poller already running — start_poller() called twice")
        return
    interval = _get_poll_interval()
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(poll_once, "interval", seconds=interval, id="orchestrator_supabase_poll")
    _scheduler.start()
    log.info("Orchestrator poller started (every %ss)", interval)


def stop_poller() -> None:
    """Called from app/main.py's lifespan on shutdown."""
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None
    log.info("Orchestrator poller stopped")
