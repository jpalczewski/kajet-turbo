"""drop active_workspaces

Revision ID: d5489d7585c7
Revises: 7e97140a40ac
Create Date: 2026-09-05 20:03:44.402465

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5489d7585c7"
down_revision: str | Sequence[str] | None = "7e97140a40ac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_table("active_workspaces")


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table(
        "active_workspaces",
        sa.Column("user_id", sa.TEXT(), nullable=False),
        sa.Column("scope", sa.VARCHAR(), nullable=False),
        sa.Column("workspace", sa.VARCHAR(), nullable=False),
        sa.Column("updated_at", sa.VARCHAR(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id", "scope"),
    )
