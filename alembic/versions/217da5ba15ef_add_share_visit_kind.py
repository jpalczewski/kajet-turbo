"""Distinguish page views from content reads; existing visits are content reads."""

import sqlalchemy as sa

from alembic import op

revision = "217da5ba15ef"
down_revision = "3fb8e992286e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "note_share_link_visits",
        sa.Column("kind", sa.Text(), nullable=False, server_default="content"),
    )


def downgrade() -> None:
    with op.batch_alter_table("note_share_link_visits") as batch_op:
        batch_op.drop_column("kind")
