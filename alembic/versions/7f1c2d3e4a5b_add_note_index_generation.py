"""add note index generation

Revision ID: 7f1c2d3e4a5b
Revises: b3f14a9c2d77
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7f1c2d3e4a5b"
down_revision: str | Sequence[str] | None = "b3f14a9c2d77"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("notes", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("index_generation", sa.Integer(), nullable=False, server_default="1")
        )


def downgrade() -> None:
    with op.batch_alter_table("notes", schema=None) as batch_op:
        batch_op.drop_column("index_generation")
