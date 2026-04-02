"""Refactor for the new pricing model

Revision ID: 5c907abb7f5e
Revises: 74b7a1f39ae2
Create Date: 2026-01-12 10:04:15.201100

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5c907abb7f5e'
down_revision: Union[str, Sequence[str], None] = '74b7a1f39ae2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return column_name in {c["name"] for c in inspector.get_columns(table_name)}


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()

    if not _column_exists(bind, "request_ledger", "cost"):
        op.add_column("request_ledger", sa.Column("cost", sa.Integer(), nullable=True))

    op.execute(
        """
        UPDATE request_ledger
        SET cost = 1
        WHERE cost IS NULL
        """
    )

    op.alter_column(
        "request_ledger",
        "cost",
        existing_type=sa.Integer(),
        nullable=False,
    )

    if not _column_exists(bind, "subscription_tier", "daily_image_limit"):
        op.add_column("subscription_tier", sa.Column("daily_image_limit", sa.Integer(), nullable=True))

    op.execute(
        """
        UPDATE subscription_tier
        SET daily_image_limit = CASE
            WHEN daily_image_limit IS NOT NULL THEN daily_image_limit
            WHEN monthly_images IS NULL THEN 10
            WHEN monthly_images <= 0 THEN 0
            ELSE GREATEST(1, (monthly_images + 29) / 30)
        END
        WHERE daily_image_limit IS NULL
        """
    )

    op.alter_column(
        "subscription_tier",
        "daily_image_limit",
        existing_type=sa.Integer(),
        nullable=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    if _column_exists(bind, "subscription_tier", "daily_image_limit"):
        op.drop_column("subscription_tier", "daily_image_limit")
    if _column_exists(bind, "request_ledger", "cost"):
        op.drop_column("request_ledger", "cost")
