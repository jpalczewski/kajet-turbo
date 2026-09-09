"""add note_share_links preview_description column

Revision ID: f1a2b3c4d5e6
Revises: 3cbbd314f794
Create Date: 2026-09-09 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "3cbbd314f794"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "note_share_links",
        sa.Column(
            "preview_description",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("note_share_links", "preview_description")
