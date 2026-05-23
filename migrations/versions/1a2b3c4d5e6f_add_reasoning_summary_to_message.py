"""add reasoning_summary to message
Revision ID: 1a2b3c4d5e6f
Revises: gd2a3b4c5d6e
Create Date: 2024-05-24 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1a2b3c4d5e6f'
down_revision: Union[str, None] = 'gd2a3b4c5d6e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('message', sa.Column('reasoning_summary', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('message', 'reasoning_summary')
