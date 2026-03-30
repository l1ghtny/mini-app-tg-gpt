"""Refactor for the new pricing model Fix

Revision ID: 94b953a754ae
Revises: 5c907abb7f5e
Create Date: 2026-01-12 14:17:55.469662

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '94b953a754ae'
down_revision: Union[str, Sequence[str], None] = '5c907abb7f5e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return column_name in {c["name"] for c in inspector.get_columns(table_name)}


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()

    if not _column_exists(bind, "subscription_tier", "monthly_images"):
        op.add_column("subscription_tier", sa.Column("monthly_images", sa.Integer(), nullable=True))

    if _column_exists(bind, "subscription_tier", "daily_image_limit"):
        op.execute(
            """
            UPDATE subscription_tier
            SET monthly_images = COALESCE(monthly_images, daily_image_limit * 30, 0)
            WHERE monthly_images IS NULL
            """
        )
    else:
        op.execute(
            """
            UPDATE subscription_tier
            SET monthly_images = COALESCE(monthly_images, 0)
            WHERE monthly_images IS NULL
            """
        )

    op.alter_column(
        "subscription_tier",
        "monthly_images",
        existing_type=sa.INTEGER(),
        nullable=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    if _column_exists(bind, "subscription_tier", "monthly_images"):
        op.alter_column(
            "subscription_tier",
            "monthly_images",
            existing_type=sa.INTEGER(),
            nullable=True,
        )
