"""add ssh_keys table

Revision ID: 4152c194c063
Revises: 455c19802af8
Create Date: 2026-06-26 12:59:22.533705

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4152c194c063"
down_revision: str | Sequence[str] | None = "455c19802af8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ssh_keys",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("algorithm", sa.Text(), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("private_key_enc", sa.LargeBinary(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", name="uq_ssh_keys_user_name"),
    )
    op.create_index("ix_ssh_keys_user", "ssh_keys", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_ssh_keys_user", table_name="ssh_keys")
    op.drop_table("ssh_keys")
