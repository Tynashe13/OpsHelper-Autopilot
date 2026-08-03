"""Add data_sources table for the Data Manager

Revision ID: d4e5f6g7h8i9
Revises: c3d4e5f6g7h8
Create Date: 2026-08-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# Alembic identifies migrations by these IDs, not filenames or dates. Think
# of it as a linked list: `down_revision` points at whichever migration ran
# immediately before this one (c3d4e5f6g7h8 — the last migration that
# existed in the template before this feature was added). When you run
# `alembic upgrade head`, Alembic walks this chain in order and runs every
# migration it hasn't applied yet. Get down_revision wrong and either the
# migration silently never runs, or it runs out of order.
revision: str = "d4e5f6g7h8i9"
down_revision: Union[str, None] = "c3d4e5f6g7h8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # This function is what actually runs when you type `alembic upgrade
    # head`. Everything here should read as "build the table described in
    # models/data_source.py" — in a well-kept project the migration and the
    # model always describe the same table, just in two different languages
    # (this is raw DDL, the model is Python/SQLAlchemy).
    op.create_table(
        "data_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("system_type", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("endpoint_url", sa.String(length=500), nullable=True),
        sa.Column("config", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="unconfigured"),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Indexes make lookups on these columns fast (id lookups happen on
    # every GET/PATCH/DELETE by id; category lookups happen every time the
    # Data Manager page groups systems by category). This trades a small
    # amount of extra disk space and slightly slower writes for much
    # faster reads — the right tradeoff for a table that's read constantly
    # and written to rarely.
    op.create_index(op.f("ix_data_sources_id"), "data_sources", ["id"], unique=False)
    op.create_index(op.f("ix_data_sources_category"), "data_sources", ["category"], unique=False)

    # Seed the four systems every Round 2 Operations build needs to register:
    # one channel, one system of record, the human loop, and Auto itself.
    # `op.execute()` runs raw SQL directly — used here instead of the
    # SQLAlchemy model because migrations should stay independent of
    # application code (a migration from a year ago should still run
    # correctly even if the DataSource Python class has since changed).
    op.execute(
        """
        INSERT INTO data_sources (name, category, system_type, description, status)
        VALUES
            ('Disruption Notice Inbox', 'channel', 'email', 'Incoming disruption notices via email/Teams', 'unconfigured'),
            ('Orders & Inventory Store', 'system_of_record', 'supabase', 'Purchase orders, inventory positions, shipments', 'unconfigured'),
            ('Commander Approval Channel', 'human_loop', 'slack', 'Workbench escalations routed to the human commander', 'unconfigured'),
            ('Auto Orchestrator', 'agent_platform', 'auto', 'Orchestrator + Operators on auto.supervity.ai', 'unconfigured')
        """
    )


def downgrade() -> None:
    # `downgrade()` is the undo button — `alembic downgrade -1` would run
    # this and reverse exactly what upgrade() did. Order matters here: you
    # drop things in the REVERSE order you created them (indexes before the
    # table they're attached to), same idea as unstacking plates.
    op.drop_index(op.f("ix_data_sources_category"), table_name="data_sources")
    op.drop_index(op.f("ix_data_sources_id"), table_name="data_sources")
    op.drop_table("data_sources")
