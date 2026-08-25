"""oauth refresh token families

Refresh-token rotation now retains consumed tokens to detect replay and revoke the
active descendant. Existing access and refresh tokens are deliberately invalidated:
their pre-migration rotation history cannot be reconstructed safely.

Revision ID: b3f14a9c2d77
Revises: 79798c49ca5f
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision: str = "b3f14a9c2d77"
down_revision: str | Sequence[str] | None = "79798c49ca5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("DELETE FROM oauth_access_tokens"))
    op.execute(sa.text("DELETE FROM oauth_refresh_tokens"))
    with op.batch_alter_table("oauth_refresh_tokens") as batch_op:
        batch_op.add_column(
            sa.Column("family_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )
        batch_op.add_column(sa.Column("consumed_at", sa.Integer(), nullable=True))
        batch_op.create_index("ix_oauth_refresh_tokens_family_id", ["family_id"])
        batch_op.alter_column("family_id", nullable=False)


def downgrade() -> None:
    # A consumed row would become valid again without consumed_at, so credentials cannot
    # survive a downgrade any more safely than they can survive the upgrade.
    op.execute(sa.text("DELETE FROM oauth_access_tokens"))
    op.execute(sa.text("DELETE FROM oauth_refresh_tokens"))
    with op.batch_alter_table("oauth_refresh_tokens") as batch_op:
        batch_op.drop_index("ix_oauth_refresh_tokens_family_id")
        batch_op.drop_column("consumed_at")
        batch_op.drop_column("family_id")
