"""Add policies and policy_evaluations tables

Revision ID: e5f6g7h8i9j0
Revises: d4e5f6g7h8i9
Create Date: 2026-08-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# down_revision points at the Data Manager migration (d4e5f6g7h8i9) — this
# is what makes Alembic run them in the right order: Data Manager's table
# gets created first, then this one, continuing the same chain explained
# in alembic/versions/d4e5f6g7h8i9_add_data_sources_table.py.
revision: str = "e5f6g7h8i9j0"
down_revision: Union[str, None] = "d4e5f6g7h8i9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Two tables get created here in one migration (unusual to bundle two,
    # but they're tightly coupled — policy_evaluations always references a
    # policies row, so it makes sense to ship them together). This first
    # create_table call builds the "policies" table — should read as the
    # DDL equivalent of the Policy class in models/policy.py.
    op.create_table(
        "policies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("natural_language", sa.Text(), nullable=False),
        sa.Column("policy_type", sa.String(length=20), nullable=False, server_default="logical"),
        sa.Column("policy_scope", sa.String(length=20), nullable=False, server_default="base"),
        sa.Column("dsl", sa.JSON(), nullable=True),
        sa.Column("refined_instruction", sa.Text(), nullable=True),
        sa.Column("ai_instruction", sa.Text(), nullable=True),
        sa.Column("entity_name", sa.String(length=100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("source", sa.String(length=50), nullable=True),
        sa.Column("execution_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_policies_id"), "policies", ["id"], unique=False)
    op.create_index(op.f("ix_policies_entity_name"), "policies", ["entity_name"], unique=False)

    # Second table — the evaluation log. Note policy_id here is NOT a
    # database-enforced foreign key back to policies.id, even though it
    # obviously references it — same reasoning as the dataset's tables in
    # round2-supabase-schema.sql: keeping it soft means a policy can be
    # deleted later without Postgres blocking the delete or cascading in
    # a way that silently destroys evaluation history.
    op.create_table(
        "policy_evaluations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("policy_id", sa.String(length=36), nullable=False),
        sa.Column("policy_name", sa.String(length=255), nullable=False),
        sa.Column("entity_name", sa.String(length=100), nullable=True),
        sa.Column("entity_id", sa.String(length=255), nullable=True),
        sa.Column("matched", sa.Boolean(), nullable=False),
        sa.Column("actions_taken", sa.JSON(), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("input_snapshot", sa.JSON(), nullable=True),
        sa.Column(
            "evaluated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_policy_evaluations_id"), "policy_evaluations", ["id"], unique=False)
    op.create_index(op.f("ix_policy_evaluations_policy_id"), "policy_evaluations", ["policy_id"], unique=False)
    op.create_index(op.f("ix_policy_evaluations_entity_name"), "policy_evaluations", ["entity_name"], unique=False)
    op.create_index(op.f("ix_policy_evaluations_entity_id"), "policy_evaluations", ["entity_id"], unique=False)
    op.create_index(op.f("ix_policy_evaluations_evaluated_at"), "policy_evaluations", ["evaluated_at"], unique=False)

    # Seed 3 real starter policies so the page isn't empty on first load and
    # the "3+ active policies" requirement is visibly met from minute one.
    # Adjust natural_language/dsl to match your actual field names once
    # Recovery Planner's real Config-driven fields are wired through here.
    op.execute(
        """
        INSERT INTO policies (id, name, description, summary, natural_language, policy_type, dsl, entity_name, is_active, priority, tags, source)
        VALUES
        (
            '9317dc69-746c-43b1-8736-47d4ddfae73b',
            'Auto-Execute Low-Impact Recoveries',
            'Skip human approval when the recovery plan is cheap and fast enough to just run.',
            'Auto-approves recovery plans under the cost/time thresholds.',
            'If the recommended recovery plan costs less than 5000 MYR and completes within 48 hours, execute it automatically without human approval.',
            'logical',
            '{"conditions": [{"field": "estimated_cost", "operator": "less_than", "value": 5000}, {"field": "estimated_hours", "operator": "less_than_or_equal", "value": 48}], "actions": [{"type": "auto_execute"}], "match_mode": "all"}',
            'recovery_plan',
            true,
            10,
            '["operations", "auto-approve"]',
            'seed'
        ),
        (
            '4e2cee81-1c6e-403e-b2e9-887a1e44dc84',
            'Escalate High-Value Disruptions',
            'Require human sign-off above a spend threshold.',
            'Sends large-impact disruptions to the commander instead of auto-executing.',
            'If the recommended recovery plan costs more than 5000 MYR, require human approval before execution.',
            'logical',
            '{"conditions": [{"field": "estimated_cost", "operator": "greater_than", "value": 5000}], "actions": [{"type": "require_approval", "value": "commander"}], "match_mode": "all"}',
            'recovery_plan',
            true,
            5,
            '["operations", "escalation"]',
            'seed'
        ),
        (
            '9512649a-d96f-4333-8ef1-accb620c918a',
            'Contract Escalation Clause Guard',
            'Blocks the cheapest option when it would breach a contract escalation clause, even if the numbers look best.',
            'Vetoes plans that violate contract terms regardless of cost savings.',
            'If a candidate recovery option would breach a contract''s escalation clause (for example, expediting beyond the allowed threshold), do not select it even if it is the cheapest or fastest option. Flag it and explain the conflict, then recommend the next-best compliant option instead.',
            'natural_language',
            NULL,
            'recovery_plan',
            true,
            1,
            '["operations", "compliance", "contracts"]',
            'seed'
        )
        """
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_policy_evaluations_evaluated_at"), table_name="policy_evaluations")
    op.drop_index(op.f("ix_policy_evaluations_entity_id"), table_name="policy_evaluations")
    op.drop_index(op.f("ix_policy_evaluations_entity_name"), table_name="policy_evaluations")
    op.drop_index(op.f("ix_policy_evaluations_policy_id"), table_name="policy_evaluations")
    op.drop_index(op.f("ix_policy_evaluations_id"), table_name="policy_evaluations")
    op.drop_table("policy_evaluations")

    op.drop_index(op.f("ix_policies_entity_name"), table_name="policies")
    op.drop_index(op.f("ix_policies_id"), table_name="policies")
    op.drop_table("policies")
