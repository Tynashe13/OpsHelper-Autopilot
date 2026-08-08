"""Add last_polled_at to data_sources for the Orchestrator poller

Revision ID: a2b3c4d5e6f7
Revises: f6g7h8i9j0k1
Create Date: 2026-08-08 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, None] = "f6g7h8i9j0k1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "data_sources",
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("data_sources", "last_polled_at")
