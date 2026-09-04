"""add user timezone and locale

Revision ID: d0143ba33003
Revises: c8e2a4f91d7b
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision: str = "d0143ba33003"
down_revision: str | Sequence[str] | None = "c8e2a4f91d7b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "timezone",
                sqlmodel.sql.sqltypes.AutoString(),
                nullable=False,
                server_default="Europe/Warsaw",
            )
        )
        batch_op.add_column(
            sa.Column(
                "locale",
                sqlmodel.sql.sqltypes.AutoString(),
                nullable=False,
                server_default="pl",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("locale")
        batch_op.drop_column("timezone")
