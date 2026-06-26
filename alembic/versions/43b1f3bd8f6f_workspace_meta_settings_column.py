"""workspace_meta settings column

Revision ID: 43b1f3bd8f6f
Revises: 4598c1375d03
Create Date: 2026-06-26 20:01:08.137411

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "43b1f3bd8f6f"
down_revision: str | Sequence[str] | None = "4598c1375d03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("workspace_meta", schema=None) as batch_op:
        batch_op.add_column(sa.Column("settings", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("workspace_meta", schema=None) as batch_op:
        batch_op.drop_column("settings")
