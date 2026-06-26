"""dangling_links table

Revision ID: 14932fd60df2
Revises: 43b1f3bd8f6f
Create Date: 2026-06-26 21:14:17.332691

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "14932fd60df2"
down_revision: str | Sequence[str] | None = "43b1f3bd8f6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "dangling_links",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("workspace", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("owner_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source_note_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("target_folder", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("target_title", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("dangling_links", schema=None) as batch_op:
        batch_op.create_index("ix_dangling_source", ["source_note_id"], unique=False)
        batch_op.create_index(
            "ix_dangling_target",
            ["workspace", "owner_id", "target_folder", "target_title"],
            unique=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("dangling_links", schema=None) as batch_op:
        batch_op.drop_index("ix_dangling_target")
        batch_op.drop_index("ix_dangling_source")

    op.drop_table("dangling_links")
