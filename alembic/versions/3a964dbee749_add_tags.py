"""add tags

Revision ID: 3a964dbee749
Revises: f8250264e0ee
Create Date: 2026-06-13 17:11:39.177595

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3a964dbee749"
down_revision: str | Sequence[str] | None = "f8250264e0ee"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "tags",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("workspace", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("owner_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("path", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("parent_id", sa.Text(), sa.ForeignKey("tags.id"), nullable=True),
        sa.Column("created_at", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("tags", schema=None) as batch_op:
        batch_op.create_index(
            "ix_tags_ws_owner_path", ["workspace", "owner_id", "path"], unique=True
        )
        batch_op.create_index(
            "ix_tags_ws_owner_parent", ["workspace", "owner_id", "parent_id"], unique=False
        )

    op.create_table(
        "note_tags",
        sa.Column("note_id", sa.Text(), sa.ForeignKey("notes.id"), nullable=False),
        sa.Column("tag_id", sa.Text(), sa.ForeignKey("tags.id"), nullable=False),
        sa.Column("source", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.PrimaryKeyConstraint("note_id", "tag_id"),
    )
    with op.batch_alter_table("note_tags", schema=None) as batch_op:
        batch_op.create_index("ix_note_tags_tag", ["tag_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("note_tags", schema=None) as batch_op:
        batch_op.drop_index("ix_note_tags_tag")
    op.drop_table("note_tags")
    with op.batch_alter_table("tags", schema=None) as batch_op:
        batch_op.drop_index("ix_tags_ws_owner_parent")
        batch_op.drop_index("ix_tags_ws_owner_path")
    op.drop_table("tags")
