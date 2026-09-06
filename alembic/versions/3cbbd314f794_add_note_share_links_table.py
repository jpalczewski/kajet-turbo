"""add note_share_links table

Revision ID: 3cbbd314f794
Revises: d5489d7585c7
Create Date: 2026-09-06 21:28:40.498041

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3cbbd314f794"
down_revision: str | Sequence[str] | None = "d5489d7585c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "note_share_links",
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("note_id", sa.Text(), nullable=False),
        sa.Column("workspace", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("revoked_at", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["note_id"], ["notes.id"]),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("token"),
    )
    op.create_index("ix_note_share_links_note", "note_share_links", ["note_id"])
    op.create_index("ix_note_share_links_owner", "note_share_links", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_note_share_links_owner", table_name="note_share_links")
    op.drop_index("ix_note_share_links_note", table_name="note_share_links")
    op.drop_table("note_share_links")
