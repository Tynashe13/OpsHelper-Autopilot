# app/services/orchestrator_poller.py
"""
Orchestrator poller — the automatic trigger for incoming records sitting
in the Supabase system-of-record table, so the Orchestrator pipeline
doesn't only react to manually-POSTed events. Same AsyncIOScheduler
pattern as services/workbench_scheduler.py, on a short interval
(ORCHESTRATOR_POLL_INTERVAL_SECONDS, default 30s).

Each tick: fetch rows newer than the last successful poll -> for each
row, run the exact same services/orchestrator_engine.process_event()
chain the manual POST /api/orchestrator/events endpoint uses -> advance
the DataSource row's `last_polled_at` high-water mark.

No-ops entirely (logs once, doesn't schedule anything) if SUPABASE_DB_URL
isn't set — see start_poller() below — so an app without Supabase
configured starts and runs identically to before this module existed.

DUPLICATE-PROCESSING SAFETY NET: process_event() is called here with
dedupe=True, which independently checks "does a WorkbenchItem already
exist for this (entity_name, entity_id, source) triple" before creating
another one — regardless of whether the cursor math below is exactly
right. This is deliberate belt-and-suspenders: a poll tick can be
re-triggered (process restart mid-batch, a slow tick overlapping the
next scheduled one, a manual re-run for debugging) and this app should
never be able to create two Workbench items for the same upstream row
because of it. The cursor (`last_polled_at`) is still the primary
mechanism keeping each tick's query small — the dedupe check is what
keeps a cursor mistake from becoming a duplicate exception a human has
to notice and clean up by hand.
"""
import logging
import os
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ..core.database import SessionLocal
from ..core.supabase_db import is_configured
from ..models.data_source import DataSource, DataSourceCategory, DataSourceStatus
from .orchestrator_engine import process_event
from .system_of_record import SystemOfRecordError, fetch_new_records, latest_updated_at

log = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

_JOB_ID = "orchestrator_supabase_poll"
_DEFAULT_INTERVAL_SECONDS = 30


def _get_interval_seconds() -> int:
    try:
        return int(os.getenv("ORCHESTRATOR_POLL_INTERVAL_SECONDS", str(_DEFAULT_INTERVAL_SECONDS)))
    except ValueError:
        log.warning(
            "ORCHESTRATOR_POLL_INTERVAL_SECONDS=%r is not a valid integer — falling back to %ds",
            os.getenv("ORCHESTRATOR_POLL_INTERVAL_SECONDS"), _DEFAULT_INTERVAL_SECONDS,
        )
        return _DEFAULT_INTERVAL_SECONDS


async def _poll_once() -> None:
    """
    One tick, pulled out of the APScheduler job wrapper so it can be
    called directly in tests/manual verification (same convention as
    services/workbench_scheduler.py's _check_overdue_items()).

    Owns its own DB session (SessionLocal, not the request-scoped
    get_db()) — this runs outside any HTTP request.
    """
    db = SessionLocal()
    try:
        sources = (
            db.query(DataSource)
            .filter(
                DataSource.category == DataSourceCategory.SYSTEM_OF_RECORD,
                DataSource.system_type == "supabase",
            )
            .all()
        )
        if not sources:
            log.warning(
                "Orchestrator poller: no DataSource row with category=system_of_record, "
                "system_type=supabase found — nothing to poll against. "
                "(Expected the seeded 'Orders & Inventory Store' row.)"
            )
            return

        entity_name = os.getenv("SUPABASE_SOR_ENTITY_NAME", "disruption")
        now = datetime.now(timezone.utc)

        for src in sources:
            try:
                rows = fetch_new_records(since=src.last_polled_at, limit=100)
            except SystemOfRecordError as exc:
                src.status = DataSourceStatus.DOWN
                src.error_message = str(exc)
                src.last_checked_at = now
                db.commit()
                log.error("Orchestrator poller: fetch failed for DataSource %s: %s", src.id, exc)
                continue

            if not rows:
                src.status = DataSourceStatus.HEALTHY
                src.last_checked_at = now
                src.last_success_at = now
                src.error_message = None
                db.commit()
                continue

            log.info("Orchestrator poller: processing %d new record(s) from '%s'", len(rows), src.name)
            for row in rows:
                try:
                    result = await process_event(
                        db,
                        entity_name=entity_name,
                        record=row,
                        source="supabase_poller",
                        actor=None,
                        request=None,
                        dedupe=True,  # see module docstring — the duplicate safety net
                    )
                    log.info(
                        "Orchestrator poller: record id=%s -> decision=%s priority=%s",
                        row.get("id"), result.decision, result.priority,
                    )
                except Exception as exc:  # noqa: BLE001 — one bad row must not stop the batch or the cursor
                    log.error("Orchestrator poller: process_event failed for row id=%s: %s", row.get("id"), exc)

            # Advance the cursor to the max updated_at actually seen in
            # this batch — NOT datetime.now() (see system_of_record.py's
            # latest_updated_at docstring for why using the data's own
            # timestamp, not this server's clock, is what avoids clock-
            # skew bugs). Only advances if the batch had at least one
            # parseable timestamp; an all-unparseable batch leaves the
            # cursor untouched rather than silently skipping rows.
            new_mark = latest_updated_at(rows)
            if new_mark is not None:
                src.last_polled_at = new_mark
            src.status = DataSourceStatus.HEALTHY
            src.last_checked_at = now
            src.last_success_at = now
            src.error_message = None
            db.commit()
    finally:
        db.close()


async def _job() -> None:
    try:
        await _poll_once()
    except Exception as exc:  # noqa: BLE001 — one bad tick should never kill the scheduler itself
        log.error("Orchestrator poller: tick failed: %s", exc)


def start_poller() -> None:
    """Called from app/main.py's lifespan on startup. No-ops (logs and
    returns) if SUPABASE_DB_URL isn't set — an app without Supabase
    configured should start and behave identically to before this module
    existed, not fail or spam errors every interval."""
    if not is_configured():
        log.info("Orchestrator poller: SUPABASE_DB_URL not set — poller not started.")
        return

    interval = _get_interval_seconds()
    if scheduler.get_job(_JOB_ID) is None:
        scheduler.add_job(_job, "interval", seconds=interval, id=_JOB_ID, replace_existing=True)
    if not scheduler.running:
        scheduler.start()
    log.info("Orchestrator poller started (%ds interval)", interval)


def stop_poller() -> None:
    """Called from app/main.py's lifespan on shutdown. Safe to call even
    if start_poller() no-op'd (scheduler was never started)."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
    log.info("Orchestrator poller stopped")
