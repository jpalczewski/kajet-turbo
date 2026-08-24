"""oauth tokens carry user_id

Identity used to be derived at call time as "the last user who authorized this
client_id" — client_authorizations is keyed on client_id alone and written with
INSERT OR REPLACE, so a second user authorizing the same client silently re-pointed
every token ever issued to that client at them. Tokens now carry their own owner.

Backfill takes the currently recorded owner for each client. A token whose client has
no authorization row gets NULL and stops resolving, which forces one re-authorization
— the safe direction, since the alternative is guessing whose token it is.

Revision ID: 79798c49ca5f
Revises: d94724e08b63
Create Date: 2026-08-24 09:16:39.707413

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "79798c49ca5f"
down_revision: str | Sequence[str] | None = "d94724e08b63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TOKEN_TABLES = ("oauth_access_tokens", "oauth_refresh_tokens")


def upgrade() -> None:
    """Upgrade schema."""
    for table in (*_TOKEN_TABLES, "oauth_authorization_codes"):
        op.add_column(table, sa.Column("user_id", sa.Text(), nullable=True))
        op.execute(
            sa.text(
                f"UPDATE {table} SET user_id = ("
                "  SELECT ca.user_id FROM client_authorizations ca"
                f"  WHERE ca.client_id = {table}.client_id"
                ")"
            )
        )
    for table in _TOKEN_TABLES:
        op.create_index(f"ix_{table}_user_id", table, ["user_id"])


def downgrade() -> None:
    """Downgrade schema."""
    for table in _TOKEN_TABLES:
        op.drop_index(f"ix_{table}_user_id", table_name=table)
    for table in (*_TOKEN_TABLES, "oauth_authorization_codes"):
        op.drop_column(table, "user_id")
