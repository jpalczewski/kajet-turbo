"""scope active_workspaces by MCP session

Revision ID: 6bb594d4a5d5
Revises: 14932fd60df2
Create Date: 2026-06-27 16:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6bb594d4a5d5"
down_revision: str | Sequence[str] | None = "14932fd60df2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "active_workspaces_new",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("scope", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("workspace", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id", "scope"),
    )
    op.execute(
        "INSERT INTO active_workspaces_new (user_id, scope, workspace, updated_at) "
        "SELECT user_id, 'user', workspace, updated_at FROM active_workspaces"
    )
    op.drop_table("active_workspaces")
    op.rename_table("active_workspaces_new", "active_workspaces")


def downgrade() -> None:
    op.create_table(
        "active_workspaces_old",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("workspace", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.execute(
        "INSERT OR REPLACE INTO active_workspaces_old (user_id, workspace, updated_at) "
        "SELECT user_id, workspace, updated_at FROM active_workspaces "
        "WHERE scope = 'user'"
    )
    op.drop_table("active_workspaces")
    op.rename_table("active_workspaces_old", "active_workspaces")
