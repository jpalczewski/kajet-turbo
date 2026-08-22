"""add link reconcile dirty set

Revision ID: d94724e08b63
Revises: b5852751bd90
Create Date: 2026-08-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision: str = "d94724e08b63"
down_revision: str | Sequence[str] | None = "b5852751bd90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "link_reconcile_dirty",
        sa.Column("owner_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("workspace", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source_note_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("owner_id", "workspace", "source_note_id"),
    )


def downgrade() -> None:
    op.drop_table("link_reconcile_dirty")
