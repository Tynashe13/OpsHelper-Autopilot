# app/services/auto_client.py
"""
LLM judgment via Supervity Auto — replaces the old direct-Anthropic
llm_client.py. Per the team decision: Auto handles ALL LLM reasoning
tasks (orchestration AND policy/insight judgment), not just orchestration.

This calls a single, generic "LLM Judgment" workflow built on Auto
(auto.supervity.ai). That workflow's contract, as WE define it when
building it in Auto's UI:

    input:  `prompt` (string) — the full instruction, system rules +
            data to judge, concatenated. Auto operators take one prompt
            input, not a separate system/user pair, so this module joins
            the two before sending.
    output: the workflow's final step returns a JSON object directly as
            its `outputs` — that whole dict IS the parsed result. Do NOT
            configure the Auto workflow to return a JSON-encoded STRING;
            configure its last step to output structured fields, so
            `activityRuns[-1].outputs` already IS the dict callers want.

Reference: https://auto.supervity.ai/docs/api-docs/workflow-runs
           https://auto.supervity.ai/docs/api-docs/authentication

Known gap: Auto's docs did not render a full example response body for
POST /workflow-runs/execute when this was written. This module assumes
the blocking endpoint returns the same shape as the documented SSE
`result` event: {"success": true, "workflowRun": {...}}. If a real call
against Auto returns a different shape, AutoOperatorError below will
surface the raw response so this is easy to fix in one place.
"""
import asyncio
import json
import logging
import os

import httpx

log = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://auto.supervity.ai/api/v1"
_TERMINAL_STATUSES = {"completed", "failed", "error"}


class AutoOperatorError(Exception):
    """Raised when a call to an Auto workflow fails or returns an
    unexpected shape. Callers should catch this the same way they'd have
    caught the old llm_client failures — log, then return a safe
    fallback rather than a 500."""


def _get_config() -> tuple[str, str, str, str]:
    """
    Reads Auto connection config from the environment. Raises a clear
    RuntimeError (not a cryptic KeyError) if something required is
    missing, naming exactly which env var needs setting.
    """
    api_key = os.getenv("AUTO_API_KEY")
    org_key = os.getenv("AUTO_ORG_KEY")
    workflow_id = os.getenv("AUTO_LLM_JUDGMENT_WORKFLOW_ID")
    base_url = os.getenv("AUTO_API_BASE_URL", _DEFAULT_BASE_URL)

    missing = [
        name
        for name, val in [
            ("AUTO_API_KEY", api_key),
            ("AUTO_ORG_KEY", org_key),
            ("AUTO_LLM_JUDGMENT_WORKFLOW_ID", workflow_id),
        ]
        if not val
    ]
    if missing:
        raise RuntimeError(
            f"Missing required env var(s) for Auto integration: {', '.join(missing)}. "
            "Set these in .env — see .env.example for where to get each value."
        )

    return api_key, org_key, workflow_id, base_url


async def complete_json(system_prompt: str, user_prompt: str, max_tokens: int = 1500) -> dict:
    """
    Drop-in replacement for the old llm_client.complete_json — same
    signature, same return type (a parsed dict) — so every existing call
    site in routers/ai_policies.py and services/policy_engine.py needs
    only its import line changed, nothing else.

    Runs the "LLM Judgment" workflow on Auto synchronously and returns
    its structured output as a dict.
    """
    api_key, org_key, workflow_id, base_url = _get_config()

    # Auto operators take a single prompt input, not a separate
    # system/user pair — join them the same way a chat completion would
    # concatenate a system message ahead of the user turn.
    combined_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "x-source": "external",
        "x-active-org": org_key,
    }

    # multipart/form-data per the documented contract: workflowId is a
    # plain form field, `inputs` is a JSON object passed as a form field
    # value (not a JSON request body).
    data = {
        "workflowId": workflow_id,
        "inputs": json.dumps({"prompt": combined_prompt, "max_tokens": max_tokens}),
    }

    url = f"{base_url}/workflow-runs/execute"

    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            response = await client.post(url, headers=headers, data=data)
        except httpx.RequestError as exc:
            log.error("Auto API request failed: %s", exc)
            raise AutoOperatorError(f"Could not reach Auto API: {exc}") from exc

    if response.status_code != 200:
        log.error(
            "Auto workflow-runs/execute returned %s: %s",
            response.status_code,
            response.text[:1000],
        )
        raise AutoOperatorError(
            f"Auto API returned {response.status_code}: {response.text[:500]}"
        )

    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        log.error("Auto API returned non-JSON response: %s", response.text[:500])
        raise AutoOperatorError("Auto API returned a non-JSON response") from exc

    workflow_run = payload.get("workflowRun")
    if not workflow_run:
        log.error("Auto API response missing 'workflowRun': %s", payload)
        raise AutoOperatorError(
            "Unexpected Auto API response shape (no 'workflowRun' key) — "
            "verify this against a real response and adjust auto_client.py"
        )

    status = workflow_run.get("status")
    if status != "completed":
        log.error("Auto workflow run did not complete: status=%s run=%s", status, workflow_run)
        raise AutoOperatorError(f"Auto workflow run ended with status '{status}', not 'completed'")

    activity_runs = workflow_run.get("activityRuns") or []
    if not activity_runs:
        log.error("Auto workflow run completed with no activity runs: %s", workflow_run)
        raise AutoOperatorError("Auto workflow run completed but returned no step outputs")

    # The final step's outputs ARE the result — see module docstring for
    # why the Auto workflow must be configured this way.
    final_outputs = activity_runs[-1].get("outputs")
    if not isinstance(final_outputs, dict):
        log.error("Final activity run outputs were not a JSON object: %s", final_outputs)
        raise AutoOperatorError(
            "Final Auto workflow step did not return a JSON object as outputs — "
            "check the 'LLM Judgment' workflow's last-step output configuration on Auto"
        )

    return final_outputs


# =============================================================================
# ORCHESTRATOR TRIGGER — used by routers/ai_manager.py, the "AI Manager"
# chat/orchestration surface. This is a SEPARATE workflow from the "LLM
# Judgment" one above: it triggers the live Ops Helper orchestrator (which
# itself coordinates the 5+ Operators built on auto.supervity.ai), not a
# single judgment call. Policy Engine / Insights stay on complete_json()
# above — this function is only ever called from the AI Manager surface.
# =============================================================================


def _get_orchestrator_config() -> tuple[str, str, str, str]:
    """Same shape as _get_config() above, but for the orchestrator
    workflow specifically — a distinct workflow ID from the LLM Judgment
    one, since it's a different Auto workflow (Ops Helper, which fans out
    to Impact Finder / Recovery Finder / Recovery Planner / Human Approval
    Operator / Execute Human Choice / Auto Execute Plan / Check Outlook
    for Notices)."""
    api_key = os.getenv("AUTO_API_KEY")
    org_key = os.getenv("AUTO_ORG_KEY")
    workflow_id = os.getenv("AUTO_ORCHESTRATOR_WORKFLOW_ID")
    base_url = os.getenv("AUTO_API_BASE_URL", _DEFAULT_BASE_URL)

    missing = [
        name
        for name, val in [
            ("AUTO_API_KEY", api_key),
            ("AUTO_ORG_KEY", org_key),
            ("AUTO_ORCHESTRATOR_WORKFLOW_ID", workflow_id),
        ]
        if not val
    ]
    if missing:
        raise RuntimeError(
            f"Missing required env var(s) for the Auto orchestrator: {', '.join(missing)}. "
            "Set these in .env — AUTO_ORCHESTRATOR_WORKFLOW_ID is the Ops Helper "
            "orchestrator workflow's ID on auto.supervity.ai, distinct from "
            "AUTO_LLM_JUDGMENT_WORKFLOW_ID."
        )

    return api_key, org_key, workflow_id, base_url


def _format_run_result(run_id: str | None, status: str | None, workflow_run: dict) -> dict:
    """Shapes a raw Auto workflowRun object into the
    {run_id, status, outputs, activity_runs} dict routers/ai_manager.py
    hands back to the frontend as the run trace."""
    activity_runs = workflow_run.get("activityRuns") or []
    final_outputs: dict = {}
    if activity_runs:
        last_outputs = activity_runs[-1].get("outputs")
        if isinstance(last_outputs, dict):
            final_outputs = last_outputs
    return {
        "run_id": run_id,
        "status": status or "unknown",
        "outputs": final_outputs,
        "activity_runs": activity_runs,
    }


async def trigger_orchestrator_run(
    record: dict,
    source: str = "ai_manager",
    max_poll_seconds: float = 90.0,
) -> dict:
    """
    Triggers the live Ops Helper orchestrator workflow on Auto and returns
    its full run trace — the "what the Orchestrator did" payload the AI
    Manager chat surface relays back to the person (see
    routers/ai_manager.py's docstring: "a chat and orchestration surface
    where a person asks the operation questions and watches the
    Orchestrator delegate to Operators and report back").

    Defensive against Auto's execute endpoint sometimes blocking until the
    run completes and sometimes returning immediately (still running): if
    the initial response already carries a terminal status, that's used
    directly. Otherwise this polls GET /workflow-runs/{run_id} on an
    exponential backoff (2s, 3s, 4.5s, ... capped at 10s) until a
    terminal status is reached or max_poll_seconds elapses.

    Raises AutoOperatorError on any failure — callers (routers/ai_manager.py)
    turn that into a 503, since this is a real external dependency that can
    be degraded independent of this app's own health.
    """
    api_key, org_key, workflow_id, base_url = _get_orchestrator_config()

    headers = {
        "Authorization": f"Bearer {api_key}",
        "x-source": "external",
        "x-active-org": org_key,
    }
    data = {
        "workflowId": workflow_id,
        "inputs": json.dumps({**record, "source": source}),
    }
    url = f"{base_url}/workflow-runs/execute"

    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            response = await client.post(url, headers=headers, data=data)
        except httpx.RequestError as exc:
            log.error("Auto orchestrator request failed: %s", exc)
            raise AutoOperatorError(f"Could not reach Auto API: {exc}") from exc

        if response.status_code != 200:
            log.error(
                "Auto orchestrator workflow-runs/execute returned %s: %s",
                response.status_code,
                response.text[:1000],
            )
            raise AutoOperatorError(
                f"Auto API returned {response.status_code}: {response.text[:500]}"
            )

        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            log.error("Auto orchestrator returned non-JSON response: %s", response.text[:500])
            raise AutoOperatorError("Auto API returned a non-JSON response") from exc

        workflow_run = payload.get("workflowRun") or {}
        run_id = workflow_run.get("id") or payload.get("runId") or payload.get("id")
        status = workflow_run.get("status") or payload.get("status")

        # execute() already blocked to a terminal status — use it directly.
        if status in _TERMINAL_STATUSES:
            return _format_run_result(run_id, status, workflow_run)

        if not run_id:
            log.error("Auto orchestrator run returned no run id to poll: %s", payload)
            raise AutoOperatorError(
                "Auto API response had no run id to poll — cannot confirm orchestrator status"
            )

        # execute() didn't block — poll for completion.
        elapsed = 0.0
        delay = 2.0
        poll_url = f"{base_url}/workflow-runs/{run_id}"
        while elapsed < max_poll_seconds:
            await asyncio.sleep(delay)
            elapsed += delay
            delay = min(delay * 1.5, 10.0)

            try:
                poll_response = await client.get(poll_url, headers=headers)
            except httpx.RequestError as exc:
                log.warning("Auto orchestrator poll failed (will retry): %s", exc)
                continue

            if poll_response.status_code != 200:
                log.warning(
                    "Auto orchestrator poll for run %s returned %s",
                    run_id,
                    poll_response.status_code,
                )
                continue

            try:
                poll_payload = poll_response.json()
            except json.JSONDecodeError:
                log.warning("Auto orchestrator poll for run %s returned non-JSON", run_id)
                continue

            poll_run = poll_payload.get("workflowRun") or poll_payload
            poll_status = poll_run.get("status")
            if poll_status in _TERMINAL_STATUSES:
                return _format_run_result(poll_run.get("id") or run_id, poll_status, poll_run)

    raise AutoOperatorError(
        f"Auto orchestrator run {run_id} did not reach a terminal status within "
        f"{max_poll_seconds:.0f}s — it may still be running; check auto.supervity.ai directly"
    )
