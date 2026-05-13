"""add image energy budgeting

Revision ID: c4d5e6f7a8b9
Revises: b1c2d3e4f5a6
Create Date: 2026-05-04 08:20:00.000000
"""

from __future__ import annotations

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    has_daily_limit = _column_exists(inspector, "subscription_tier", "daily_image_limit")
    if not _column_exists(inspector, "subscription_tier", "daily_image_energy"):
        op.add_column(
            "subscription_tier",
            sa.Column("daily_image_energy", sa.Integer(), nullable=False, server_default="0"),
        )

    if has_daily_limit:
        op.execute(
            sa.text(
                """
                UPDATE subscription_tier
                SET daily_image_energy = COALESCE(daily_image_limit, 0)
                WHERE COALESCE(daily_image_energy, 0) = 0
                """
            )
        )
    else:
        op.execute(
            sa.text(
                """
                UPDATE subscription_tier
                SET daily_image_energy = CASE
                    WHEN COALESCE(monthly_images, 0) <= 0 THEN 0
                    ELSE GREATEST(1, ((monthly_images + 29) / 30))
                END
                WHERE COALESCE(daily_image_energy, 0) = 0
                """
            )
        )
    op.execute(
        sa.text(
            """
            UPDATE subscription_tier
            SET daily_image_energy = 100
            WHERE lower(name) = 'basic'
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE subscription_tier
            SET daily_image_energy = 300
            WHERE lower(name) IN ('pro', 'advanced')
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE subscription_tier
            SET daily_image_energy = 600
            WHERE lower(name) = 'premium'
            """
        )
    )

    # Safety net for private or custom tiers during rollout:
    # if they still have zero energy but have monthly image allowance,
    # derive a daily baseline from monthly allowance.
    op.execute(
        sa.text(
            """
            UPDATE subscription_tier
            SET daily_image_energy = GREATEST(1, ((monthly_images + 29) / 30))
            WHERE COALESCE(daily_image_energy, 0) = 0
              AND COALESCE(monthly_images, 0) > 0
              AND is_active = TRUE
            """
        )
    )

    inspector = sa.inspect(bind)
    if _column_exists(inspector, "subscription_tier", "daily_image_limit"):
        op.drop_column("subscription_tier", "daily_image_limit")

    image_quality_pricing = sa.Table(
        "image_quality_pricing",
        sa.MetaData(),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("image_model", sa.String()),
        sa.Column("quality", sa.String()),
        sa.Column("credit_cost", sa.Float()),
        sa.Column("is_active", sa.Boolean()),
    )
    target_rows = [
        {"image_model": "gpt-image-1.5", "quality": "low", "credit_cost": 10.0},
        {"image_model": "gpt-image-1.5", "quality": "medium", "credit_cost": 40.0},
        {"image_model": "gpt-image-1.5", "quality": "high", "credit_cost": 150.0},
        {"image_model": "gpt-image-2", "quality": "low", "credit_cost": 20.0},
        {"image_model": "gpt-image-2", "quality": "medium", "credit_cost": 60.0},
        {"image_model": "gpt-image-2", "quality": "high", "credit_cost": 250.0},
    ]

    for row in target_rows:
        existing = bind.execute(
            sa.select(image_quality_pricing.c.id).where(
                image_quality_pricing.c.image_model == row["image_model"],
                image_quality_pricing.c.quality == row["quality"],
            )
        ).first()
        if existing:
            bind.execute(
                image_quality_pricing.update()
                .where(image_quality_pricing.c.id == existing.id)
                .values(credit_cost=row["credit_cost"], is_active=True)
            )
        else:
            bind.execute(
                image_quality_pricing.insert().values(
                    id=uuid.uuid4(),
                    image_model=row["image_model"],
                    quality=row["quality"],
                    credit_cost=row["credit_cost"],
                    is_active=True,
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _column_exists(inspector, "subscription_tier", "daily_image_limit"):
        op.add_column(
            "subscription_tier",
            sa.Column("daily_image_limit", sa.Integer(), nullable=False, server_default="0"),
        )
        op.execute(sa.text("UPDATE subscription_tier SET daily_image_limit = COALESCE(daily_image_energy, 0)"))

    inspector = sa.inspect(bind)
    if _column_exists(inspector, "subscription_tier", "daily_image_energy"):
        op.drop_column("subscription_tier", "daily_image_energy")
