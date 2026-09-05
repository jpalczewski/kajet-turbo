"""add jobs priority column

Per-kind priority for JobRepository.claim (#151): lower priority claims first, breaking
the single next_run_at FIFO into lanes so a bulk fan-out (embed_note/reindex_note) cannot
park user-visible jobs (push_workspace/reconcile_links) behind it.

Revision ID: 6bbe78a9932a
Revises: d0143ba33003
Create Date: 2026-09-05 08:57:46.720623

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6bbe78a9932a"
down_revision: str | Sequence[str] | None = "d0143ba33003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
    )
    op.drop_index("ix_jobs_claim", table_name="jobs")
    op.create_index("ix_jobs_claim", "jobs", ["status", "priority", "next_run_at"])


def downgrade() -> None:
    op.drop_index("ix_jobs_claim", table_name="jobs")
    op.create_index("ix_jobs_claim", "jobs", ["status", "next_run_at"])
    op.drop_column("jobs", "priority")
