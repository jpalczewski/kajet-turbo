"""add note share link visits

Revision ID: 3fb8e992286e
Revises: f1a2b3c4d5e6
Create Date: 2026-09-09 01:42:21.817232

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3fb8e992286e"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "note_share_link_visits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("ip", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["token"], ["note_share_links.token"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_note_share_link_visits_token_created_at",
        "note_share_link_visits",
        ["token", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_note_share_link_visits_token_created_at", table_name="note_share_link_visits")
    op.drop_table("note_share_link_visits")
