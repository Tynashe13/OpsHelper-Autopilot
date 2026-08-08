"""Add workbench_items table

Revision ID: f6g7h8i9j0k1
Revises: e5f6g7h8i9j0
Create Date: 2026-08-08 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# down_revision points at the policies migration (e5f6g7h8i9j0) — the real
# current head of this repo's chain as of this migration being written.
revision: str = "f6g7h8i9j0k1"
down_revision: Union[str, None] = "e5f6g7h8i9j0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workbench_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("entity_name", sa.String(length=100), nullable=True),
        sa.Column("entity_id", sa.String(length=255), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=False, server_default="manual"),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("priority", sa.String(length=20), nullable=False, server_default="medium"),
        sa.Column("notify_target", sa.String(length=255), nullable=True),
        sa.Column("assigned_to", sa.String(length=255), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("retry_interval_minutes", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("escalated_to", sa.String(length=255), nullable=True),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.String(length=255), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_workbench_items_id"), "workbench_items", ["id"], unique=False)
    op.create_index(op.f("ix_workbench_items_entity_name"), "workbench_items", ["entity_name"], unique=False)
    op.create_index(op.f("ix_workbench_items_entity_id"), "workbench_items", ["entity_id"], unique=False)
    op.create_index(op.f("ix_workbench_items_status"), "workbench_items", ["status"], unique=False)
    op.create_index(op.f("ix_workbench_items_priority"), "workbench_items", ["priority"], unique=False)
    op.create_index(op.f("ix_workbench_items_next_retry_at"), "workbench_items", ["next_retry_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_workbench_items_next_retry_at"), table_name="workbench_items")
    op.drop_index(op.f("ix_workbench_items_priority"), table_name="workbench_items")
    op.drop_index(op.f("ix_workbench_items_status"), table_name="workbench_items")
    op.drop_index(op.f("ix_workbench_items_entity_id"), table_name="workbench_items")
    op.drop_index(op.f("ix_workbench_items_entity_name"), table_name="workbench_items")
    op.drop_index(op.f("ix_workbench_items_id"), table_name="workbench_items")
    op.drop_table("workbench_items")
