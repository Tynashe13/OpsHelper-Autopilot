# app/services/llm_client.py
"""
Direct-Anthropic LLM client — the fallback services/policy_engine.py uses
for natural-language policy judgment while the real Auto integration
(services/auto_client.py) is a stretch goal, not a blocker.

Why this exists: auto_client.py's complete_json() calls Auto's
POST /workflow-runs/execute and expects a synchronous
{"workflowRun": {"status": "completed", ...}} response. Per the Round 2
handoff doc's own investigation (§4), that endpoint is actually
fire-and-forget — it returns 202 Accepted / {"accepted": true, "message":
"Execution started"} with no run ID and no synchronous result at all.
Calling it the way auto_client.py does raises AutoOperatorError on every
single call. This module restores the pre-Auto behavior so the Policy
Engine and Insights actually produce results for natural_language
policies, instead of every evaluation failing closed with "Evaluation
failed, treated as no-match" (see policy_engine.py's
_evaluate_natural_language, which already handles that failure gracefully
— but a Policy Engine that always fails closed isn't actually
demonstrating anything).

Same signature and return type as auto_client.complete_json on purpose:
a parsed dict, not a JSON string — so the only change needed anywhere
else in the codebase is policy_engine.py's import line.
"""
import json
import logging
import os

from anthropic import AsyncAnthropic, AnthropicError

log = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-sonnet-5"


class LLMClientError(Exception):
    """Raised when a call to Anthropic's API fails or returns something
    that can't be parsed as JSON. Callers (policy_engine.py) already
    catch broad Exception around complete_json() and fail closed to
    "no-match" rather than crash — this class exists mainly so logs are
    easy to grep for LLM-specific failures vs. everything else."""


def _get_client() -> AsyncAnthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Missing required env var ANTHROPIC_API_KEY for LLM-backed policy/insight "
            "judgment. Set it in .env — see .env.example."
        )
    return AsyncAnthropic(api_key=api_key)


def _strip_code_fence(text: str) -> str:
    """The system prompts calling this (see policy_engine.py's
    _NL_SYSTEM_PROMPT) already ask for raw JSON with no markdown, but
    models don't always comply — this strips a ```json ... ``` or ``` ...
    ``` wrapper if one shows up, rather than letting json.loads fail on
    something that's actually valid JSON underneath three backticks."""
    stripped = text.strip()
    if stripped.startswith("```"):
        # Drop the opening fence (with or without a language tag) and the
        # closing fence, keeping whatever's between them.
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else stripped[3:]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    return stripped.strip()


async def complete_json(system_prompt: str, user_prompt: str, max_tokens: int = 1500) -> dict:
    """
    Drop-in replacement for auto_client.complete_json — same signature,
    same return type (a parsed dict) — so every existing call site in
    services/policy_engine.py needs only its import line changed.

    Sends system_prompt as the Messages API's `system` parameter (a real
    system/user split, unlike auto_client.py's workaround of concatenating
    them into one prompt for Auto's single-input operators) and expects
    the model's entire text response to be a JSON object.
    """
    model = os.getenv("ANTHROPIC_MODEL", _DEFAULT_MODEL)
    client = _get_client()

    try:
        response = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except AnthropicError as exc:
        log.error("Anthropic API call failed: %s", exc)
        raise LLMClientError(f"Anthropic API call failed: {exc}") from exc

    # response.content is a list of content blocks — for a plain-text
    # response (no tool use) this is a single TextBlock, and .text is its
    # string content. Concatenating all text blocks (rather than assuming
    # exactly one) is defensive in case a future SDK/model version splits
    # the response across more than one block.
    text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
    if not text:
        log.error("Anthropic response had no text content: %s", response)
        raise LLMClientError("Anthropic response contained no text content")

    cleaned = _strip_code_fence(text)
    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        log.error("Anthropic response was not valid JSON: %s", text[:500])
        raise LLMClientError(f"Anthropic response was not valid JSON: {exc}") from exc

    if not isinstance(result, dict):
        log.error("Anthropic response JSON was not an object: %s", result)
        raise LLMClientError("Anthropic response JSON was not an object")

    return result
