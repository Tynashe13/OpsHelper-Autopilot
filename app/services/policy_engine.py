# app/services/policy_engine.py
"""
Policy Engine — runtime evaluation, not authoring.

This is the piece the Round 2 brief actually means by "policies evaluated
at runtime": given a business record (a disruption notice, a PO line,
whatever your entity_name maps to), decide which active policies apply,
whether they matched, and what actions they call for. The AI Policies page
(CreateWithAI, RuleBuilderModal, PolicyEditModal) is authoring UI on top of
this — this module is what actually gets called when your Orchestrator (or
the FastAPI endpoint it hits) needs a real decision.

Two evaluation paths:
- policy_type == "logical": deterministic DSL evaluation, no LLM call,
  fast and free.
- policy_type == "natural_language": no DSL exists, so the record and the
  policy's instruction are sent to the LLM to judge — slower, costs a
  call, but handles policies too nuanced for simple conditions (e.g. "cheapest
  option unless it breaches a contract escalation clause").

Every evaluation — matched or not — is written to both the audit log and
the policy_evaluations table. That satisfies the "every policy evaluation
logged" requirement and doubles as raw material for the Insights pipeline.
"""
import logging
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from ..models.policy import Policy as PolicyModel
from ..models.policy import PolicyEvaluation
from ..schemas.policy import PolicyAction, PolicyEvaluationResult
from .audit import audit
from .llm_client import complete_json

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Deterministic DSL evaluation
# ---------------------------------------------------------------------------

# Each entry maps an operator string (what a policy's DSL says, e.g.
# "greater_than") to the actual Python function that checks it. `lambda` is
# just a one-line anonymous function — `lambda field_val, target: field_val
# == target` is a shorthand for a full `def` that takes two arguments and
# returns whether they're equal. Storing functions in a dictionary like
# this (instead of one giant if/elif/elif chain) is what makes adding a
# new operator later a one-line change instead of restructuring code.
_OPERATORS = {
    "equals": lambda field_val, target: field_val == target,
    "not_equals": lambda field_val, target: field_val != target,
    # These four convert both sides to numbers first via _num() below,
    # because a value coming from JSON/a database could arrive as a string
    # ("500") instead of a real number (500) — comparing "500" < 600 with
    # Python's normal < would either error or compare alphabetically, both
    # wrong. _num() forces both sides into actual floats first.
    "less_than": lambda field_val, target: _num(field_val) < _num(target),
    "less_than_or_equal": lambda field_val, target: _num(field_val) <= _num(target),
    "greater_than": lambda field_val, target: _num(field_val) > _num(target),
    "greater_than_or_equal": lambda field_val, target: _num(field_val) >= _num(target),
    # `field_val or ""` guards against field_val being None — without it,
    # "x in None" would crash with a TypeError instead of just returning False.
    "contains": lambda field_val, target: target in (field_val or ""),
    "not_contains": lambda field_val, target: target not in (field_val or ""),
    "in": lambda field_val, target: field_val in (target or []),
    "not_in": lambda field_val, target: field_val not in (target or []),
    "is_empty": lambda field_val, _target: field_val in (None, "", []),
    "is_not_empty": lambda field_val, _target: field_val not in (None, "", []),
}


def _num(value: Any) -> float:
    """
    Safely converts anything to a float for numeric comparisons. If the
    value can't be converted (e.g. it's the word "unknown" instead of a
    number), returns NaN ("Not a Number") instead of crashing — and NaN
    has a special property in Python/math that ANY comparison against it
    (NaN < 5, NaN > 5, NaN == NaN) returns False. That's exactly the
    behavior we want: a malformed field should make the condition fail
    safely, not crash the whole evaluation.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _evaluate_dsl(dsl: dict, record: dict) -> tuple[bool, str]:
    """
    The actual deterministic rule-checker. Takes a policy's DSL (the
    {conditions, actions, match_mode} structure) and a record (a plain
    dict — e.g. a recovery plan's cost/timing), and returns whether the
    policy's conditions matched, plus a human-readable explanation string
    that gets stored/logged either way.
    """
    conditions = dsl.get("conditions", [])
    match_mode = dsl.get("match_mode", "all")

    if not conditions:
        # A policy with no conditions can't meaningfully "match" anything —
        # treat this as a safe no-match rather than accidentally matching
        # every record (which an empty `all()` check would otherwise do,
        # since all([]) is True in Python — this explicit check avoids that trap).
        return False, "No conditions defined"

    results = []  # True/False per condition, e.g. [True, False, True]
    reasons = []  # human-readable string per condition, for the explanation

    for cond in conditions:
        field, operator, target = cond.get("field"), cond.get("operator"), cond.get("value")
        # record.get(field) — if the record doesn't have this field at
        # all, this returns None rather than raising an error. That None
        # then flows into the operator function above, which (for numeric
        # comparisons) becomes NaN and safely fails the condition — this
        # is the "don't invent missing values, fail safe instead" behavior
        # the hackathon brief specifically asks for, implemented here at
        # the lowest level.
        field_val = record.get(field)
        op_fn = _OPERATORS.get(operator)
        if op_fn is None:
            # The policy's DSL used an operator string we don't recognize
            # (typo, or something added to the frontend but not here yet).
            # Fail safe: log a warning and count this condition as False,
            # rather than crashing the whole evaluation for one bad condition.
            log.warning("Unknown policy operator '%s' — treating as no-match", operator)
            results.append(False)
            reasons.append(f"{field} {operator} {target}: unknown operator")
            continue
        try:
            passed = bool(op_fn(field_val, target))
        except Exception as exc:  # noqa: BLE001 — a bad record shouldn't crash evaluation
            # Belt-and-suspenders on top of the NaN handling above — if
            # some other unexpected error happens evaluating this one
            # condition, don't let it take down evaluation of every other
            # policy in the same batch.
            passed = False
            log.warning("Condition evaluation error on field '%s': %s", field, exc)
        results.append(passed)
        # !r in an f-string calls repr() on the value — shows strings with
        # quotes around them (e.g. 'MYR' instead of MYR) which makes the
        # logged explanation much easier to read when debugging later.
        reasons.append(f"{field}({field_val!r}) {operator} {target!r} → {passed}")

    # all(results) is Python's built-in "are every one of these True?" —
    # any(results) is "is at least one True?". This is exactly what
    # match_mode "all" (AND) vs "any" (OR) means from the DSL.
    matched = all(results) if match_mode == "all" else any(results)
    explanation = f"[{match_mode}] " + "; ".join(reasons)
    return matched, explanation


# ---------------------------------------------------------------------------
# Natural-language evaluation (LLM-backed)
# ---------------------------------------------------------------------------

_NL_SYSTEM_PROMPT = """You are a policy enforcement engine for a procurement \
exception command center. You are given one business policy written in plain \
English and one business record. Decide whether the policy applies to this \
record and, if so, what action it calls for.

Return ONLY JSON, no markdown, no preamble, in this exact shape:
{
  "matched": true or false,
  "actions": [{"type": "string", "value": "string or null"}],
  "explanation": "one or two sentences on why, referencing specific record fields"
}

If the policy doesn't clearly apply, return matched: false with a brief \
explanation of why not. Never guess at missing fields — if you cannot tell \
whether the policy applies because a needed field is missing from the \
record, return matched: false and say exactly what's missing."""


async def _evaluate_natural_language(instruction: str, record: dict) -> tuple[bool, list[dict], str]:
    """
    The LLM-backed counterpart to _evaluate_dsl above — used when a policy
    doesn't have a DSL (policy_type == "natural_language"). Builds one
    prompt combining the policy's instruction text and the record's data,
    sends it to Claude via complete_json (see llm_client.py), and unpacks
    the structured JSON response.
    """
    user_prompt = f"POLICY:\n{instruction}\n\nRECORD:\n{record}"
    try:
        result = await complete_json(_NL_SYSTEM_PROMPT, user_prompt, max_tokens=500)
        # .get(key, default) on the result dict, in case the LLM omitted a
        # field despite instructions — this way a slightly incomplete
        # response degrades gracefully instead of raising a KeyError.
        return bool(result.get("matched")), result.get("actions", []), result.get("explanation", "")
    except Exception as exc:  # noqa: BLE001 — never let an LLM hiccup break the run
        # If the API call fails entirely (network issue, bad API key,
        # rate limit, malformed JSON that complete_json couldn't parse),
        # this is the safety net: treat it as "did not match" rather than
        # crashing the whole batch of policy evaluations. A failed check
        # should never look like a passed one.
        log.error("Natural-language policy evaluation failed: %s", exc)
        return False, [], f"Evaluation failed, treated as no-match: {exc}"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def evaluate_policies_for_entity(
    db: Session,
    entity_name: str,
    record: dict,
    actor: dict | None = None,
    request: Request | None = None,
) -> list[PolicyEvaluationResult]:
    """
    THE function everything else in this file exists to support. Called
    from routers/ai_policies.py's /evaluate endpoint, and meant to also be
    called directly from wherever your Orchestrator-integration backend
    code makes a decision.

    Evaluates every active policy scoped to `entity_name` (plus any
    entity-less policies, which apply everywhere) against `record`.
    Persists + audit-logs every evaluation, matched or not.
    """
    # This query is the actual "which policies apply here" logic:
    #   - is_active must be True (deactivated policies are skipped entirely)
    #   - entity_name must either match exactly, OR be unset on the policy
    #     (a policy with no entity_name is treated as a global rule that
    #     applies to every kind of record)
    #   - ordered by priority ascending, so lower-numbered (higher
    #     priority) policies are evaluated first — useful if you later want
    #     "first match wins" behavior, though right now every policy is
    #     always evaluated regardless of order.
    policies = (
        db.query(PolicyModel)
        .filter(PolicyModel.is_active.is_(True))
        .filter((PolicyModel.entity_name == entity_name) | (PolicyModel.entity_name.is_(None)))
        .order_by(PolicyModel.priority.asc())
        .all()
    )

    results: list[PolicyEvaluationResult] = []

    # One pass through every applicable policy — this is the fan-out point
    # where the two evaluation paths (DSL vs LLM) actually split.
    for policy in policies:
        if policy.policy_type == "logical" and policy.dsl:
            # Fast path: no network call, pure Python.
            matched, explanation = _evaluate_dsl(policy.dsl, record)
            actions = policy.dsl.get("actions", []) if matched else []
        else:
            # Slow path: an LLM call happens here. Falls back to this path
            # even for a "logical" policy that's somehow missing its dsl —
            # better to attempt a judgment call than silently skip the policy.
            instruction = policy.refined_instruction or policy.ai_instruction or policy.natural_language
            matched, actions, explanation = await _evaluate_natural_language(instruction, record)

        # Build the in-memory result object returned to whoever called
        # this function (e.g. the /evaluate API endpoint's HTTP response).
        results.append(
            PolicyEvaluationResult(
                policy_id=policy.id,
                policy_name=policy.name,
                matched=matched,
                # `actions` might already be PolicyAction objects (from a
                # DSL) or plain dicts (from the LLM's JSON response) — this
                # normalizes either shape into PolicyAction objects so the
                # caller always gets a consistent type back.
                actions=[PolicyAction(**a) if isinstance(a, dict) else a for a in actions],
                explanation=explanation,
            )
        )

        # Persist structured row for Insights aggregation. `db.add()` only
        # stages this — nothing is actually written until db.commit() at
        # the very end of the function, after the whole loop finishes.
        db.add(
            PolicyEvaluation(
                policy_id=policy.id,
                policy_name=policy.name,
                entity_name=entity_name,
                entity_id=str(record.get("id", "")) or None,
                matched=matched,
                actions_taken=actions if matched else [],
                explanation=explanation,
                input_snapshot=record,
            )
        )
        if matched:
            # `(policy.execution_count or 0)` guards against execution_count
            # somehow being None instead of 0 — defensive, cheap insurance.
            policy.execution_count = (policy.execution_count or 0) + 1

        # Audit trail — satisfies "every policy evaluation logged". Note
        # this happens INSIDE the loop, once per policy, not once for the
        # whole batch — so if 5 policies get checked against one record,
        # this produces 5 separate audit log entries, one per policy.
        await audit.log(
            action="policy.evaluate",
            description=f"Policy '{policy.name}' evaluated against {entity_name}: "
            f"{'matched' if matched else 'no match'}",
            actor=actor,
            category="data",
            resource_type="policy",
            resource_id=policy.id,
            resource_name=policy.name,
            metadata={"entity_name": entity_name, "matched": matched, "explanation": explanation},
            request=request,
        )

    # One single commit at the end, after the whole loop — this means all
    # the PolicyEvaluation rows (and the execution_count updates) for this
    # entire batch are saved to the database together, as one atomic unit,
    # rather than one slower round-trip to Postgres per policy.
    db.commit()
    return results
