"""merge search, release-readiness, and access-path heads

Revision ID: z1a2b3c4d5e6
Revises: cs1a2b3c4d5e, dca1ce1aecc2, p1a2b3c4d5e6
Create Date: 2026-06-11 18:05:00.000000
"""

from typing import Sequence, Union


revision: str = "z1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = (
    "cs1a2b3c4d5e",
    "dca1ce1aecc2",
    "p1a2b3c4d5e6",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
