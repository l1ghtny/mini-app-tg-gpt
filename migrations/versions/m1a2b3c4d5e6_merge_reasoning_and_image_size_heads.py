"""merge reasoning and image size heads

Revision ID: m1a2b3c4d5e6
Revises: 1a2b3c4d5e6f, h1a2b3c4d5e6
Create Date: 2026-05-24 13:30:00.000000

"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "m1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = ("1a2b3c4d5e6f", "h1a2b3c4d5e6")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

