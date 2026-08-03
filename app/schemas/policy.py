# app/schemas/policy.py
"""
Pydantic schemas for the AI Policies pillar. This file is organized into
three groups, matching the three different "jobs" the Policy Engine does:

  1. Core CRUD shapes — creating/editing/listing policies (the boring,
     necessary part)
  2. AI-analysis request/response shapes — what the LLM-backed "help me
     write this policy" endpoints send and receive
  3. Runtime evaluation shapes — what gets sent when actually CHECKING a
     policy against a real record (this is the part judges are scoring)

As in schemas/data_source.py: these describe API request/response shapes,
NOT the database — that's models/policy.py's job. FastAPI validates every
incoming request against these automatically.
"""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Core Policy CRUD — shapes match frontend/src/components/ai/policies/PolicyCard.tsx
# ---------------------------------------------------------------------------


class PolicyCondition(BaseModel):
    """One comparison, e.g. {"field": "amount", "operator": "greater_than",
    "value": 5000}. See services/policy_engine.py's _OPERATORS dict for
    every operator string this can actually use."""

    field: str
    operator: str
    value: Any = None  # deliberately untyped — could be a number, string, or list depending on the operator


class PolicyAction(BaseModel):
    """What should happen if the policy's conditions match, e.g.
    {"type": "require_approval", "value": "commander"}. The Policy Engine
    itself doesn't DO these actions — it just reports which ones should
    fire; whatever calls /evaluate is responsible for actually acting on them."""

    type: str
    value: Any = None
    params: Optional[dict] = None


class PolicyDSL(BaseModel):
    """
    The structured form of a "logical" policy — conditions + actions +
    how to combine them. `match_mode: "all"` means every condition must be
    true (AND logic); `"any"` means at least one must be true (OR logic).
    """

    conditions: list[PolicyCondition] = Field(default_factory=list)
    actions: list[PolicyAction] = Field(default_factory=list)
    match_mode: str = "all"  # 'all' | 'any'
    stop_on_match: bool = False


class PolicyBase(BaseModel):
    name: str
    description: str = ""
    natural_language: str
    policy_type: str = "logical"  # 'logical' | 'natural_language'
    policy_scope: str = "base"    # 'base' | 'instruction' | 'custom'
    refined_instruction: Optional[str] = None
    entity_name: Optional[str] = None
    priority: int = 50
    tags: list[str] = Field(default_factory=list)
    is_active: bool = True
    dsl: Optional[PolicyDSL] = None


class PolicyCreate(PolicyBase):
    pass


class PolicyUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    natural_language: Optional[str] = None
    policy_type: Optional[str] = None
    refined_instruction: Optional[str] = None
    entity_name: Optional[str] = None
    priority: Optional[int] = None
    tags: Optional[list[str]] = None
    is_active: Optional[bool] = None
    dsl: Optional[PolicyDSL] = None


class PolicyResponse(BaseModel):
    id: str
    name: str
    description: str
    summary: Optional[str] = None
    natural_language: str
    policy_type: str
    policy_scope: str = "base"
    dsl: Optional[dict] = None
    refined_instruction: Optional[str] = None
    ai_instruction: Optional[str] = None
    entity_name: Optional[str] = None
    is_active: bool
    priority: int
    tags: list[str]
    source: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    execution_count: int
    last_executed_at: Optional[datetime] = None

    class Config:
        orm_mode = True


# ---------------------------------------------------------------------------
# AI-analysis endpoints — shapes match CreateWithAI.tsx / RuleBuilderModal.tsx
# ---------------------------------------------------------------------------


class AnalyzeInputRequest(BaseModel):
    input: str


class AnalysisResult(BaseModel):
    suggested_type: str  # 'logical' | 'natural_language'
    confidence: float
    reason: str
    suggested_name: str
    summary: str
    dsl: Optional[PolicyDSL] = None
    refined_instruction: Optional[str] = None
    entity_name: Optional[str] = None
    suggested_tags: list[str] = Field(default_factory=list)


class CheckConflictsRequest(BaseModel):
    natural_language: str
    policy_scope: str = "base"
    entity_name: Optional[str] = None


class AnalyzeRuleRequest(BaseModel):
    natural_language: str
    policy_type: Optional[str] = None
    entity_name: Optional[str] = None


class RuleConflict(BaseModel):
    conflicting_rule_id: str
    conflicting_rule_name: str
    explanation: str


class RuleOverride(BaseModel):
    overridden_rule_id: str
    overridden_rule_name: str
    explanation: str


class ConflictResult(BaseModel):
    conflicts: list[RuleConflict] = Field(default_factory=list)
    overrides: list[RuleOverride] = Field(default_factory=list)
    clarifications: list[str] = Field(default_factory=list)
    suggested_instructions: list[str] = Field(default_factory=list)
    refined_instruction: str
    is_valid: bool = True
    warnings: Optional[list[str]] = None


class TranslateRequest(BaseModel):
    natural_language: str


class TranslateResult(BaseModel):
    dsl: PolicyDSL
    confidence: float


# ---------------------------------------------------------------------------
# Runtime evaluation — used by the Orchestrator-integration layer, not the
# authoring UI. This is the part judges actually mean by "runtime evaluation".
# ---------------------------------------------------------------------------


class EvaluateRequest(BaseModel):
    """What you send to POST /api/ai/policies/evaluate. `entity_name`
    picks which policies apply (only ones matching this entity, plus any
    with no entity_name set at all — see policy_engine.py's query filter).
    `record` is the actual business data to check, e.g. a recovery plan's
    cost and timing — any shape works, it's just a dict."""

    entity_name: str
    record: dict


class PolicyEvaluationResult(BaseModel):
    """One policy's verdict on one record — the building block of
    EvaluateResponse below."""

    policy_id: str
    policy_name: str
    matched: bool
    actions: list[PolicyAction] = Field(default_factory=list)
    explanation: str


class EvaluateResponse(BaseModel):
    """The full result of checking every active policy against one record —
    `evaluations` has one entry per policy that was checked, `matched_count`
    is a quick summary of how many actually applied."""

    entity_name: str
    evaluations: list[PolicyEvaluationResult]
    matched_count: int
