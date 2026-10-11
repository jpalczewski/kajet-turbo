"""add notices table

Revision ID: 0d1944d88146
Revises: 217da5ba15ef
Create Date: 2026-10-11 14:46:31.057141

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0d1944d88146"
down_revision: str | Sequence[str] | None = "217da5ba15ef"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "notices",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.Float(), nullable=False),
        sa.Column("last_seen_at", sa.Float(), nullable=False),
        sa.Column("resolved_at", sa.Float(), nullable=True),
        sa.Column("acknowledged_at", sa.Float(), nullable=True),
        sa.Column("last_delivered_at", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "key", name="uq_notices_user_key"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("notices")
