"""add folder to notes

Revision ID: a1b2c3d4e5f6
Revises: 886a2bdbc536
Create Date: 2026-06-09 20:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '886a2bdbc536'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('notes', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('folder', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default='')
        )


def downgrade() -> None:
    with op.batch_alter_table('notes', schema=None) as batch_op:
        batch_op.drop_column('folder')
