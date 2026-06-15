"""make welcoming bonus daily budget

Revision ID: k1a2b3c4d5e6
Revises: j1a2b3c4d5e6
Create Date: 2026-06-15 11:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "k1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "j1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


WELCOMING_BONUS_ALIASES = ("welcoming bonus", "welcoming_bonus", "bonus")
FAST_BUCKET_MODELS = ("gpt-5.4-nano", "gemini-3.1-flash-lite")


def upgrade() -> None:
    bind = op.get_bind()

    tier_rows = bind.execute(
        sa.text(
            """
            SELECT id
            FROM subscription_tier
            WHERE lower(name) IN :aliases
            """
        ).bindparams(sa.bindparam("aliases", expanding=True)),
        {"aliases": list(WELCOMING_BONUS_ALIASES)},
    ).all()

    for row in tier_rows:
        bind.execute(
            sa.text(
                """
                UPDATE subscription_tier
                SET monthly_images = 0,
                    daily_image_energy = 80,
                    is_recurring = true
                WHERE id = :tier_id
                """
            ),
            {"tier_id": row.id},
        )
        bind.execute(
            sa.text(
                """
                UPDATE tier_model_limit
                SET monthly_requests = 15,
                    daily_requests = 15
                WHERE tier_id = :tier_id
                  AND model_name IN :models
                """
            ).bindparams(sa.bindparam("models", expanding=True)),
            {"tier_id": row.id, "models": list(FAST_BUCKET_MODELS)},
        )


def downgrade() -> None:
    bind = op.get_bind()

    tier_rows = bind.execute(
        sa.text(
            """
            SELECT id
            FROM subscription_tier
            WHERE lower(name) IN :aliases
            """
        ).bindparams(sa.bindparam("aliases", expanding=True)),
        {"aliases": list(WELCOMING_BONUS_ALIASES)},
    ).all()

    for row in tier_rows:
        bind.execute(
            sa.text(
                """
                UPDATE subscription_tier
                SET monthly_images = 80,
                    daily_image_energy = 0,
                    is_recurring = false
                WHERE id = :tier_id
                """
            ),
            {"tier_id": row.id},
        )
        bind.execute(
            sa.text(
                """
                UPDATE tier_model_limit
                SET monthly_requests = 15,
                    daily_requests = 15
                WHERE tier_id = :tier_id
                  AND model_name IN :models
                """
            ).bindparams(sa.bindparam("models", expanding=True)),
            {"tier_id": row.id, "models": list(FAST_BUCKET_MODELS)},
        )
