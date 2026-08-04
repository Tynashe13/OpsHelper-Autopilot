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
import json
import logging
import os

import httpx

log = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://auto.supervity.ai/api/v1"


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
