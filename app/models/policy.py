# app/models/policy.py
"""
AI Policy model — backs the "AI Policies" pillar.

Column names deliberately mirror the frontend's `Policy` interface
(frontend/src/components/ai/policies/PolicyCard.tsx) field-for-field so the
ORM object serializes straight into what the UI already expects — no
translation layer needed between DB and the 4+ policy components that were
already built against this exact shape.
"""
import uuid

# JSON column type lets us store a nested structure (like the DSL's
# conditions/actions list) directly in one Postgres column instead of
# needing separate tables for it — Postgres stores and lets you query JSON
# natively, so this is a legitimate design choice, not a shortcut.
from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from ..core.database import Base


def _new_id() -> str:
    """Generates a random unique ID string, e.g. 'a1b2c3d4-....'. Used as
    the default value for Policy.id below — every new policy gets one of
    these instead of a simple counting number (1, 2, 3...)."""
    return str(uuid.uuid4())


class Policy(Base):
    """
    One row = one business rule (e.g. "escalate anything over 5000 MYR").
    Column names deliberately mirror the frontend's `Policy` interface
    (frontend/src/components/ai/policies/PolicyCard.tsx) field-for-field so
    the ORM object serializes straight into what the UI already expects —
    no translation layer needed between DB and the 4+ policy components
    that were already built against this exact shape.
    """

    __tablename__ = "policies"

    # String(36) UUID instead of an auto-incrementing integer, on purpose:
    # the frontend's existing Policy type expects `id: string`, and a UUID
    # can be generated safely before the row is even saved (unlike an
    # auto-increment number, which only exists after the database assigns
    # it) — `default=_new_id` means Python generates it the moment a new
    # Policy object is created, no round-trip to the database required.
    id = Column(String(36), primary_key=True, default=_new_id, index=True)

    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=False, default="")
    summary = Column(Text, nullable=True)
    natural_language = Column(Text, nullable=False)  # the rule, as the human originally typed it

    # These two fields control HOW a policy gets evaluated — see
    # services/policy_engine.py, which branches its logic based on
    # policy_type specifically.
    policy_type = Column(String(20), nullable=False, default="logical")  # 'logical' | 'natural_language'
    policy_scope = Column(String(20), nullable=False, default="base")    # 'base' | 'instruction' | 'custom'

    # Only populated when policy_type == "logical" — this is the
    # structured {conditions, actions, match_mode} shape the DSL evaluator
    # in policy_engine.py reads directly.
    dsl = Column(JSON, nullable=True)  # {conditions, actions, match_mode, stop_on_match}
    refined_instruction = Column(Text, nullable=True)  # cleaned-up version of natural_language, from the LLM
    ai_instruction = Column(Text, nullable=True)  # what actually gets sent to the LLM at evaluation time
    entity_name = Column(String(100), nullable=True, index=True)  # what kind of record this applies to

    is_active = Column(Boolean, nullable=False, default=True)  # inactive policies are skipped at evaluation time
    priority = Column(Integer, nullable=False, default=50)  # lower number = evaluated first
    tags = Column(JSON, nullable=False, default=list)  # e.g. ["operations", "compliance"]
    source = Column(String(50), nullable=True)  # "manual" | "seed" | wherever the policy came from

    # Bookkeeping fields, updated by services/policy_engine.py every time
    # this policy actually matches something at runtime.
    execution_count = Column(Integer, nullable=False, default=0)
    last_executed_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PolicyEvaluation(Base):
    """
    One row per runtime policy evaluation — this table is the actual proof
    that "policies are evaluated at runtime and every evaluation is
    logged." Every single check (whether the policy matched or not) gets a
    row here. Also mirrored into the audit log (category='data',
    action='policy.evaluate') for the same requirement, but kept here too
    as structured, queryable rows the Insights pipeline can aggregate over
    directly — e.g. "how often did the Contract Escalation Clause Guard
    actually fire this week" is one SQL query against this table.
    """

    __tablename__ = "policy_evaluations"

    # Plain auto-incrementing integer here (unlike Policy.id above) because
    # nothing outside the backend ever needs to reference a specific
    # evaluation row by ID — it's an internal log, not something a frontend
    # form edits.
    id = Column(Integer, primary_key=True, index=True)
    policy_id = Column(String(36), nullable=False, index=True)  # which policy was checked
    policy_name = Column(String(255), nullable=False)  # denormalized copy of the name, so this row is
                                                          # still readable even if the policy is later renamed/deleted
    entity_name = Column(String(100), nullable=True, index=True)
    entity_id = Column(String(255), nullable=True, index=True)  # which specific record was checked

    matched = Column(Boolean, nullable=False)  # did the policy's condition(s) apply to this record?
    actions_taken = Column(JSON, nullable=True)  # list[PolicyAction] that fired, if matched
    explanation = Column(Text, nullable=True)  # human-readable reason, from the DSL evaluator or the LLM
    input_snapshot = Column(JSON, nullable=True)  # the record the policy was evaluated against —
                                                    # kept so you can reconstruct exactly what happened later

    evaluated_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
