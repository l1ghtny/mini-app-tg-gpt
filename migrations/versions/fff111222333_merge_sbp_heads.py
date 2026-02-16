"""merge sbp heads

Revision ID: fff111222333
Revises: 69a355643294, f1a2b3c4d5e6
Create Date: 2026-02-15 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'fff111222333'
down_revision: Union[str, Sequence[str], None] = ('69a355643294', 'f1a2b3c4d5e6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
