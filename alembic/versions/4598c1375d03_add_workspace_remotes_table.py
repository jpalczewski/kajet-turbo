"""add workspace_remotes table

Revision ID: 4598c1375d03
Revises: 4152c194c063
Create Date: 2026-06-26 14:02:31.608148

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4598c1375d03"
down_revision: str | Sequence[str] | None = "4152c194c063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_remotes",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("workspace", sa.Text(), nullable=False),
        sa.Column("origin_url", sa.Text(), nullable=False),
        sa.Column("ssh_key_id", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("dirty_at", sa.Text(), nullable=True),
        sa.Column("pushed_at", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["ssh_key_id"], ["ssh_keys.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("user_id", "workspace"),
    )


def downgrade() -> None:
    op.drop_table("workspace_remotes")
