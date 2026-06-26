"""add jobs table

Revision ID: 455c19802af8
Revises: 99ed9715ba79
Create Date: 2026-06-26 12:03:08.832185

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "455c19802af8"
down_revision: str | Sequence[str] | None = "99ed9715ba79"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=True),
        sa.Column("dedup_key", sa.Text(), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.Float(), nullable=False),
        sa.Column("locked_by", sa.Text(), nullable=True),
        sa.Column("locked_at", sa.Float(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jobs_claim", "jobs", ["status", "next_run_at"])
    op.create_index("ix_jobs_user_id", "jobs", ["user_id"])
    op.create_index(
        "uq_jobs_pending_dedup",
        "jobs",
        ["kind", "dedup_key"],
        unique=True,
        sqlite_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("uq_jobs_pending_dedup", table_name="jobs")
    op.drop_index("ix_jobs_user_id", table_name="jobs")
    op.drop_index("ix_jobs_claim", table_name="jobs")
    op.drop_table("jobs")
