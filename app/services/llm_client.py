# app/services/llm_client.py
"""
Thin LLM wrapper for the AI Policies and AI Insights pillars.

The brief is explicit: Auto powers agent orchestration, but policies and
insights can use *any* LLM. This defaults to Claude (Anthropic API) since
it needs zero extra account setup beyond an API key, but every call goes
through this one module — swap providers here only, nothing else in the
app needs to change.

Set ANTHROPIC_API_KEY in your environment. Model defaults to
claude-sonnet-4-6; override with ANTHROPIC_MODEL if you want to pin a
different snapshot (see docs.claude.com for the current list).
"""
import json
import logging
import os

# AsyncAnthropic is Anthropic's official Python SDK client, in its async
# form — "async" here means API calls don't block the whole server while
# waiting for a response, matching the `async def` functions everywhere
# else in this backend (FastAPI is built around this pattern throughout).
from anthropic import AsyncAnthropic

log = logging.getLogger(__name__)

# Module-level variable, created once and reused — this is the "singleton"
# pattern. Without it, every single call to complete_json() would create a
# brand-new client object (and re-read the API key) for no reason.
_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    """
    Lazily creates the Anthropic client on first use, then reuses it for
    every call after that. "Lazy" means it doesn't try to read the API key
    or construct the client at import time (when the app first starts) —
    only the first time an LLM call is actually needed. This matters
    because it means the whole app can still start up and serve non-AI
    requests fine even if ANTHROPIC_API_KEY isn't set yet; you only hit the
    RuntimeError below at the moment something actually needs the LLM.
    """
    global _client  # tells Python "modify the module-level variable above, don't create a new local one"
    if _client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set — required for AI Policies "
                "analysis/translation and AI Insights generation."
            )
        _client = AsyncAnthropic(api_key=api_key)
    return _client


async def complete_json(system_prompt: str, user_prompt: str, max_tokens: int = 1500) -> dict:
    """
    Calls Claude with a system prompt that must instruct it to return ONLY
    JSON (no markdown fences, no preamble), and parses the result.

    Every caller in routers/ai_policies.py and policy_engine.py follows the
    same two-part prompt pattern: `system_prompt` sets the rules and the
    exact JSON shape expected (written once per use case, reused every
    call); `user_prompt` is the actual data for this specific call (the
    policy text, the record being checked, etc.).

    Raises on API failure or unparseable output — callers decide how to
    degrade (see routers/ai_policies.py for the pattern: catch, log, return
    a safe fallback rather than a 500).
    """
    client = _get_client()
    # os.getenv(key, default) — reads the env var, but falls back to
    # "claude-sonnet-4-6" if ANTHROPIC_MODEL was never set. This is how a
    # sensible default coexists with letting the team override it later.
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    # This is the actual API call — everything above this line is setup,
    # everything below is processing the response.
    response = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    # Claude's response can technically contain multiple "blocks" (text,
    # tool calls, etc.) — this line concatenates just the text parts. In
    # practice for these prompts there's only ever one text block, but
    # writing it this way is safe even if that ever changes.
    text = "".join(block.text for block in response.content if block.type == "text")
    text = text.strip()
    if text.startswith("```"):
        # Belt-and-suspenders: even though every system prompt says "no
        # markdown fences", models sometimes add them anyway. This strips
        # a leading ```json (or plain ```) and trailing ``` if present,
        # so json.loads() below doesn't choke on stray fence characters.
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        # Log the actual bad output (truncated to 500 chars so logs don't
        # balloon) so whoever's debugging can see exactly what the model
        # returned, then re-raise as a different exception type so callers
        # have one consistent error to catch regardless of whether the
        # failure was a JSON problem or something else.
        log.error("LLM returned non-JSON output: %s", text[:500])
        raise ValueError(f"LLM output was not valid JSON: {exc}") from exc
