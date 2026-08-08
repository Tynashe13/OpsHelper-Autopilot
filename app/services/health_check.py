# app/services/health_check.py
"""
Health Check Service — runs a live reachability check against a DataSource
and returns (status, latency_ms, error_message).

Design: one generic HTTP reachability check that works for anything with an
endpoint_url, plus optional per-system_type overrides where a real
authenticated call is more meaningful than a bare HTTP ping (e.g. Slack's
auth.test, Airtable's base metadata endpoint). Fill in the TODOs with your
actual credentialed calls as you wire each integration — until then every
source falls back to the generic ping so the Data Manager page never breaks.

Credentials are read from environment variables, never from the database —
DataSource.config is for non-secret metadata only (base IDs, channel names).
"""
import os
import time
from typing import Callable

# httpx is an HTTP client library (like `requests`, but supports async/await
# so it doesn't block the whole server while waiting on a slow network call).
import httpx

from ..models.data_source import DataSourceStatus

DEGRADED_LATENCY_MS = 1500  # above this, mark "degraded" even if reachable


async def _generic_ping(endpoint_url: str | None, config: str | None) -> tuple[str, float | None, str | None]:
    """
    The fallback check used for any system_type that doesn't have its own
    function below (see CHECKERS dict at the bottom). Just does a plain
    HTTP GET and times how long it takes — works for anything with a URL,
    tells you nothing about whether the *credentials* are valid, only
    whether the endpoint responds at all.

    Every checker function in this file returns the same 3-item shape:
    (status_string, latency_in_milliseconds_or_None, error_message_or_None)
    so the router (routers/data_manager.py) can treat every check the same
    way regardless of which system it was checking.
    """
    if not endpoint_url:
        # Nothing to check yet — this isn't an error, just "not set up".
        return DataSourceStatus.UNCONFIGURED, None, "No endpoint configured"

    # time.perf_counter() is a high-precision stopwatch — we start it right
    # before the network call and stop it right after, so latency_ms below
    # measures only the actual request time, not any surrounding Python work.
    start = time.perf_counter()
    try:
        # `async with` opens the HTTP client, and closes/cleans it up
        # automatically when the block ends — even if an error happens.
        # timeout=5.0 means: give up and raise an error after 5 seconds,
        # so one slow/dead system can't hang the whole health check.
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(endpoint_url)
        latency_ms = (time.perf_counter() - start) * 1000  # convert seconds -> ms

        # HTTP status code conventions: 200s = success, 400s = client-side
        # problem (bad request/auth), 500s = the *server* is broken.
        if resp.status_code >= 500:
            return DataSourceStatus.DOWN, latency_ms, f"HTTP {resp.status_code}"
        if resp.status_code >= 400:
            return DataSourceStatus.DEGRADED, latency_ms, f"HTTP {resp.status_code}"
        if latency_ms > DEGRADED_LATENCY_MS:
            # Technically reachable, but slow enough to be worth flagging —
            # this is a judgment call, tune DEGRADED_LATENCY_MS if needed.
            return DataSourceStatus.DEGRADED, latency_ms, "Slow response"
        return DataSourceStatus.HEALTHY, latency_ms, None
    except httpx.TimeoutException:
        # The 5-second timeout above was hit — the system didn't respond at all.
        return DataSourceStatus.DOWN, None, "Timed out"
    except httpx.RequestError as exc:
        # Any other network-level failure (DNS lookup failed, connection
        # refused, etc.) — httpx.RequestError is the parent class that
        # covers all of these, so this one except catches them all.
        return DataSourceStatus.DOWN, None, str(exc)


async def _check_auto_platform(endpoint_url: str | None, config: str | None) -> tuple[str, float | None, str | None]:
    """Auto by Supervity — swap this for a real call to auto.supervity.ai
    once the Workflow API key is generated (see auto.supervity.ai/docs)."""
    # os.getenv reads an environment variable — returns None if it isn't
    # set, rather than crashing, which is why we can check `if not api_key`.
    api_key = os.getenv("AUTO_API_KEY")
    if not api_key:
        return DataSourceStatus.UNCONFIGURED, None, "AUTO_API_KEY not set"
    # TODO: replace with a real orchestrator status/ping call once available.
    # For now this still just does the generic ping — having a key present
    # doesn't yet mean we've verified it's a *valid* key.
    return await _generic_ping(endpoint_url, config)


async def _check_slack(endpoint_url: str | None, config: str | None) -> tuple[str, float | None, str | None]:
    """
    Unlike the generic ping, this makes a real authenticated call —
    Slack's `auth.test` endpoint specifically checks whether a bot token is
    valid, so this tells us more than "is slack.com online" (which is
    always yes) — it tells us "does OUR credential actually work."
    """
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return DataSourceStatus.UNCONFIGURED, None, "SLACK_BOT_TOKEN not set"
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                "https://slack.com/api/auth.test",
                # Slack (like most APIs) expects the token in an
                # Authorization header, formatted as "Bearer <token>".
                headers={"Authorization": f"Bearer {token}"},
            )
        latency_ms = (time.perf_counter() - start) * 1000
        data = resp.json()
        # Slack's API quirk: it almost always returns HTTP 200 even on
        # failure, and puts the real success/failure in a JSON "ok" field
        # instead — this is why we check `data.get("ok")`, not resp.status_code.
        if data.get("ok"):
            return DataSourceStatus.HEALTHY, latency_ms, None
        return DataSourceStatus.DOWN, latency_ms, data.get("error", "auth.test failed")
    except httpx.RequestError as exc:
        return DataSourceStatus.DOWN, None, str(exc)


# This dictionary is what makes the whole file "pluggable": it maps a
# system_type string (like "slack") to the Python function that knows how
# to check it. `check_data_source()` below just looks up the right function
# and calls it — adding a new integration later means adding one function
# above and one line here, nothing else in the app needs to change.
async def _check_supabase(endpoint_url: str | None, config: str | None) -> tuple[str, float | None, str | None]:
    """
    Unlike the generic ping, this actually exercises the real read path —
    a trivial `SELECT 1` against the configured Supabase connection
    (core/supabase_db.py), not just an HTTP GET against some URL. This is
    what lets the "Orders & Inventory Store" DataSource row genuinely
    show "healthy" only once the system-of-record connection actually
    works, not just once a URL is typed into a config field somewhere.
    """
    # Local import — avoids core/supabase_db.py (and its SQLAlchemy engine
    # machinery) being imported at module load time for every process
    # that imports health_check.py, even ones that never check Supabase.
    from ..core.supabase_db import SupabaseNotConfiguredError, get_supabase_session_factory

    try:
        session_factory = get_supabase_session_factory()
    except SupabaseNotConfiguredError as exc:
        return DataSourceStatus.UNCONFIGURED, None, str(exc)

    from sqlalchemy import text

    start = time.perf_counter()
    session = session_factory()
    try:
        session.execute(text("SELECT 1"))
        latency_ms = (time.perf_counter() - start) * 1000
        if latency_ms > DEGRADED_LATENCY_MS:
            return DataSourceStatus.DEGRADED, latency_ms, "Slow response"
        return DataSourceStatus.HEALTHY, latency_ms, None
    except Exception as exc:  # noqa: BLE001 — any connection/auth/network failure lands here
        return DataSourceStatus.DOWN, None, str(exc)
    finally:
        session.close()


CHECKERS: dict[str, Callable] = {
    "auto": _check_auto_platform,
    "slack": _check_slack,
    "supabase": _check_supabase,
}


async def check_data_source(system_type: str, endpoint_url: str | None, config: str | None):
    """
    The one function the rest of the app actually calls (see
    routers/data_manager.py). Returns (status, latency_ms, error_message).

    `CHECKERS.get(system_type, _generic_ping)` means: look up system_type
    in the dict above; if it's not found (e.g. "airtable", which has no
    dedicated function yet), use _generic_ping as the default instead of
    raising an error. This is what "graceful fallback" looks like in code.
    """
    checker = CHECKERS.get(system_type, _generic_ping)
    return await checker(endpoint_url, config)
