# app/routers/ai_policies.py
"""
AI Policies endpoints.

Route surface here is dictated entirely by the frontend, which was already
built end-to-end against this exact contract (see
frontend/src/components/ai/policies/*.tsx) — CreateWithAI, RuleBuilderModal,
and PolicyEditModal all call specific endpoints below. This router exists
to make those real instead of erroring, plus adds list/delete (which the
page currently fakes with local state) and a runtime /evaluate endpoint
for the Orchestrator-integration layer.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..models.policy import Policy as PolicyModel
from ..schemas.policy import (
    AnalysisResult,
    AnalyzeInputRequest,
    AnalyzeRuleRequest,
    CheckConflictsRequest,
    ConflictResult,
    EvaluateRequest,
    EvaluateResponse,
    PolicyCreate,
    PolicyResponse,
    PolicyUpdate,
    TranslateRequest,
    TranslateResult,
)
from ..security import get_current_user
from ..services.audit import audit
from ..services.auto_client import complete_json
from ..services.policy_engine import evaluate_policies_for_entity

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/policies", tags=["AI Policies"])

# This file has 4 sections, in this order:
#   1. CRUD — plain create/read/update/delete for policies (see
#      routers/data_manager.py for the fully-commented version of this
#      pattern; it's identical here, just a different table)
#   2. LLM-backed authoring — the endpoints CreateWithAI.tsx /
#      RuleBuilderModal.tsx / PolicyEditModal.tsx call to get AI help
#      writing/checking a policy. Every one of these follows the same
#      shape: build a prompt, call complete_json(), catch failures and
#      degrade gracefully instead of crashing the authoring UI.
#   3. Runtime evaluation — the ONE endpoint (/evaluate) that actually
#      matters for judging. Thin wrapper around
#      services/policy_engine.py's evaluate_policies_for_entity().


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.get("", response_model=list[PolicyResponse])
def list_policies(db: Session = Depends(get_db)):
    return db.query(PolicyModel).order_by(PolicyModel.priority.asc(), PolicyModel.created_at.desc()).all()


@router.post("", response_model=PolicyResponse)
async def create_policy(
    payload: PolicyCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    db_policy = PolicyModel(
        name=payload.name,
        description=payload.description,
        summary=payload.description,
        natural_language=payload.natural_language,
        policy_type=payload.policy_type,
        policy_scope=payload.policy_scope,
        dsl=payload.dsl.dict() if payload.dsl else None,
        refined_instruction=payload.refined_instruction,
        ai_instruction=payload.refined_instruction or payload.natural_language,
        entity_name=payload.entity_name,
        is_active=payload.is_active,
        priority=payload.priority,
        tags=payload.tags,
        source="manual",
    )
    db.add(db_policy)
    db.commit()
    db.refresh(db_policy)

    await audit.log(
        action="policy.create",
        description=f"Created policy '{db_policy.name}'",
        actor=user,
        category="data",
        resource_type="policy",
        resource_id=db_policy.id,
        resource_name=db_policy.name,
        request=request,
    )
    return db_policy


@router.patch("/{policy_id}", response_model=PolicyResponse)
async def update_policy(
    policy_id: str,
    payload: PolicyUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    db_policy = db.query(PolicyModel).filter(PolicyModel.id == policy_id).first()
    if db_policy is None:
        raise HTTPException(status_code=404, detail="Policy not found")

    updates = payload.dict(exclude_unset=True)
    if "dsl" in updates and updates["dsl"] is not None:
        updates["dsl"] = payload.dsl.dict()
    for field, value in updates.items():
        setattr(db_policy, field, value)
    db.commit()
    db.refresh(db_policy)

    await audit.log(
        action="policy.update",
        description=f"Updated policy '{db_policy.name}'",
        actor=user,
        category="data",
        resource_type="policy",
        resource_id=db_policy.id,
        resource_name=db_policy.name,
        request=request,
    )
    return db_policy


@router.delete("/{policy_id}")
async def delete_policy(
    policy_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    db_policy = db.query(PolicyModel).filter(PolicyModel.id == policy_id).first()
    if db_policy is None:
        raise HTTPException(status_code=404, detail="Policy not found")

    name = db_policy.name
    db.delete(db_policy)
    db.commit()

    await audit.log(
        action="policy.delete",
        description=f"Deleted policy '{name}'",
        actor=user,
        category="data",
        resource_type="policy",
        resource_id=policy_id,
        request=request,
    )
    return {"message": "Policy deleted"}


# ---------------------------------------------------------------------------
# LLM-backed authoring — analyze / conflicts / translate
# ---------------------------------------------------------------------------

_ANALYZE_INPUT_SYSTEM_PROMPT = """You are a policy-authoring assistant for a \
procurement exception command center. A user has typed a plain-English \
business rule. Turn it into a structured policy suggestion.

Decide whether the rule is best expressed as a deterministic DSL ("logical") \
— simple field comparisons like amount/threshold/status — or needs natural- \
language judgment at runtime ("natural_language") — anything involving \
trade-offs, exceptions, or contextual reasoning a simple IF/THEN can't capture.

Return ONLY JSON, no markdown, in this exact shape:
{
  "suggested_type": "logical" or "natural_language",
  "confidence": 0.0 to 1.0,
  "reason": "one sentence explaining the type choice",
  "suggested_name": "short policy name, title case",
  "summary": "one sentence summary",
  "dsl": {"conditions": [{"field": "...", "operator": "...", "value": "..."}], "actions": [{"type": "...", "value": "..."}], "match_mode": "all"} or null,
  "refined_instruction": "cleaned-up instruction text" or null,
  "entity_name": "best-guess entity this applies to (e.g. purchase_order, disruption_notice, shipment), or null",
  "suggested_tags": ["lowercase-hyphenated", "tags"]
}

Valid operators: equals, not_equals, less_than, less_than_or_equal, \
greater_than, greater_than_or_equal, contains, not_contains, in, not_in, \
is_empty, is_not_empty. Only populate "dsl" if suggested_type is "logical"; \
only populate "refined_instruction" if suggested_type is "natural_language"."""


@router.post("/analyze-input", response_model=AnalysisResult)
async def analyze_input(payload: AnalyzeInputRequest):
    """
    Called by CreateWithAI.tsx when a user types a plain-English rule and
    clicks "analyze." This is the pattern every LLM-authoring endpoint in
    this file follows: (1) call complete_json with a prompt that spells out
    the exact JSON shape wanted, (2) unpack the dict straight into the
    matching Pydantic schema with `Schema(**result)` — this only works
    because the prompt's described JSON shape and the schema's fields were
    written to match exactly, (3) if anything goes wrong, return a proper
    HTTP error (502 = "the thing we depend on failed") instead of letting
    an unhandled exception produce a confusing generic 500.
    """
    try:
        result = await complete_json(_ANALYZE_INPUT_SYSTEM_PROMPT, payload.input)
        return AnalysisResult(**result)
    except Exception as exc:  # noqa: BLE001
        log.error("analyze-input failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Policy analysis failed: {exc}") from exc


_CONFLICT_SYSTEM_PROMPT = """You are checking a proposed policy against a \
procurement command center's existing active policies for conflicts and \
overrides.

Return ONLY JSON, no markdown, in this exact shape:
{
  "conflicts": [{"conflicting_rule_id": "...", "conflicting_rule_name": "...", "explanation": "..."}],
  "overrides": [{"overridden_rule_id": "...", "overridden_rule_name": "...", "explanation": "..."}],
  "clarifications": ["question strings for ambiguous cases"],
  "suggested_instructions": ["alternative phrasing suggestions"],
  "refined_instruction": "a cleaned-up version of the proposed policy text",
  "is_valid": true or false,
  "warnings": ["any other concerns"]
}

A conflict is when the new policy and an existing one could both match the \
same record and call for contradictory actions. An override is when the new \
policy is strictly more specific than an existing one (e.g. a per-vendor \
exception to a general rule) — not a conflict, just worth flagging. If there \
are no existing policies to compare against, or nothing found, return empty \
arrays and is_valid: true."""


def _format_existing_policies(db: Session, entity_name: str | None) -> str:
    query = db.query(PolicyModel).filter(PolicyModel.is_active.is_(True))
    if entity_name:
        query = query.filter(
            (PolicyModel.entity_name == entity_name) | (PolicyModel.entity_name.is_(None))
        )
    existing = query.limit(30).all()
    if not existing:
        return "No existing active policies."
    lines = [f"- id={p.id} name={p.name!r} entity={p.entity_name!r}: {p.natural_language}" for p in existing]
    return "\n".join(lines)


@router.post("/check-conflicts", response_model=ConflictResult)
async def check_conflicts(payload: CheckConflictsRequest, db: Session = Depends(get_db)):
    """
    Worth contrasting with analyze_input() above: that endpoint raises a
    502 on failure, this one doesn't. Different choice made deliberately
    for each — check-conflicts is a secondary "nice to have" check inside
    a multi-step create-policy flow, so failing soft (return an empty,
    harmless result) keeps the user's flow moving. analyze_input is the
    primary step the whole flow depends on, so failing loud there makes
    more sense — nothing useful to fall back to.
    """
    existing = _format_existing_policies(db, payload.entity_name)
    user_prompt = (
        f"PROPOSED POLICY:\n{payload.natural_language}\n\n"
        f"EXISTING ACTIVE POLICIES:\n{existing}"
    )
    try:
        result = await complete_json(_CONFLICT_SYSTEM_PROMPT, user_prompt)
        return ConflictResult(**result)
    except Exception as exc:  # noqa: BLE001
        log.error("check-conflicts failed: %s", exc)
        # Degrade gracefully — the frontend already has a local fallback for
        # this exact case, but a real response beats an error where possible.
        return ConflictResult(
            conflicts=[], overrides=[], clarifications=[], suggested_instructions=[],
            refined_instruction=payload.natural_language, is_valid=True,
            warnings=["Conflict check unavailable — LLM call failed"],
        )


@router.post("/analyze", response_model=ConflictResult)
async def analyze_rule(payload: AnalyzeRuleRequest, db: Session = Depends(get_db)):
    """Same underlying check as /check-conflicts — RuleBuilderModal calls
    this route name, CreateWithAI calls /check-conflicts. Identical contract."""
    existing = _format_existing_policies(db, payload.entity_name)
    user_prompt = (
        f"PROPOSED POLICY:\n{payload.natural_language}\n\n"
        f"EXISTING ACTIVE POLICIES:\n{existing}"
    )
    try:
        result = await complete_json(_CONFLICT_SYSTEM_PROMPT, user_prompt)
        return ConflictResult(**result)
    except Exception as exc:  # noqa: BLE001
        log.error("analyze failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Rule analysis failed: {exc}") from exc


_TRANSLATE_SYSTEM_PROMPT = """Translate a plain-English business rule into a \
structured policy DSL.

Return ONLY JSON, no markdown, in this exact shape:
{
  "dsl": {"conditions": [{"field": "...", "operator": "...", "value": "..."}], "actions": [{"type": "...", "value": "..."}], "match_mode": "all"},
  "confidence": 0.0 to 1.0
}

Valid operators: equals, not_equals, less_than, less_than_or_equal, \
greater_than, greater_than_or_equal, contains, not_contains, in, not_in, \
is_empty, is_not_empty. If the rule genuinely can't be expressed as simple \
field comparisons, do your best approximation and set confidence low."""


@router.post("/translate", response_model=TranslateResult)
async def translate_policy(payload: TranslateRequest):
    try:
        result = await complete_json(_TRANSLATE_SYSTEM_PROMPT, payload.natural_language)
        return TranslateResult(**result)
    except Exception as exc:  # noqa: BLE001
        log.error("translate failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Translation failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Runtime evaluation — the actual "policy engine" the judges are scoring
# ---------------------------------------------------------------------------


@router.post("/evaluate", response_model=EvaluateResponse)
async def evaluate(
    payload: EvaluateRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Call this from wherever your Orchestrator-integration backend logic
    makes a decision (e.g. right before recommending a recovery plan) to get
    a real, logged policy verdict instead of hardcoded business logic."""
    # This function is intentionally thin — almost all the real logic
    # lives in services/policy_engine.py. The router's job here is just:
    # unpack the validated request, call the service, and shape the
    # response — everything about HOW policies get evaluated is decided
    # somewhere else, so this endpoint stays easy to read at a glance.
    results = await evaluate_policies_for_entity(
        db, payload.entity_name, payload.record, actor=user, request=request
    )
    return EvaluateResponse(
        entity_name=payload.entity_name,
        evaluations=results,
        matched_count=sum(1 for r in results if r.matched),
    )
