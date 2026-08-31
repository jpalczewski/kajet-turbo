"""add note temporal metadata

Revision ID: c8e2a4f91d7b
Revises: 7f1c2d3e4a5b
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c8e2a4f91d7b"
down_revision: str | Sequence[str] | None = "7f1c2d3e4a5b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("notes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("occurred_at", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("period", sa.String(), nullable=True))
        batch_op.create_index(
            "ix_notes_workspace_owner_occurred_at", ["workspace", "owner_id", "occurred_at"]
        )
        batch_op.create_index(
            "ix_notes_workspace_owner_period", ["workspace", "owner_id", "period"]
        )


def downgrade() -> None:
    with op.batch_alter_table("notes", schema=None) as batch_op:
        batch_op.drop_index("ix_notes_workspace_owner_period")
        batch_op.drop_index("ix_notes_workspace_owner_occurred_at")
        batch_op.drop_column("period")
        batch_op.drop_column("occurred_at")
