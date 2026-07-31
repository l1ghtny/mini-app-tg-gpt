"""add account language and Telegram profile photo

Revision ID: y1a2b3c4d5e6
Revises: x1a2b3c4d5e6
Create Date: 2026-07-31 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "y1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "x1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "app_user",
        sa.Column("telegram_photo_url", sa.String(), nullable=True),
    )
    op.add_column(
        "app_user",
        sa.Column("preferred_language", sa.String(), nullable=True),
    )
    op.create_check_constraint(
        "ck_app_user_preferred_language",
        "app_user",
        "preferred_language IS NULL OR preferred_language IN ('en', 'ru')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_app_user_preferred_language",
        "app_user",
        type_="check",
    )
    op.drop_column("app_user", "preferred_language")
    op.drop_column("app_user", "telegram_photo_url")
