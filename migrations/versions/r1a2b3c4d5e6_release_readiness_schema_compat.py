"""release readiness schema compat revision

Revision ID: r1a2b3c4d5e6
Revises: t1a2b3c4d5e6
Create Date: 2026-06-04 10:55:00.000000
"""

from typing import Sequence, Union


revision: str = "r1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "t1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Compatibility shim for databases already stamped with this revision.
    # The actual release-readiness schema changes are applied in the
    # succeeding dca1ce1aecc2 migration, which is written defensively.
    pass


def downgrade() -> None:
    pass
