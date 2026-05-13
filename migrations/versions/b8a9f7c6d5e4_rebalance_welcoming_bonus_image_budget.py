"""rebalance welcoming bonus image budget

Revision ID: b8a9f7c6d5e4
Revises: a9b8c7d6e5f4
Create Date: 2026-05-05 14:35:00.000000
"""

from __future__ import annotations

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b8a9f7c6d5e4"
down_revision: Union[str, Sequence[str], None] = "a9b8c7d6e5f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


WELCOMING_BONUS_TIER_ID = uuid.UUID("2f9c27a3-d22e-4693-9c1b-94554a56b1e3")
IMAGE_MODELS = ("gpt-image-1.5", "gpt-image-2")
IMAGE_QUALITIES = ("low", "medium", "high")

# Conservative fixed one-time budget (credits) per image model.
# No daily refill is used for this tier.
FIXED_CREDITS_PER_MODEL = 40

SEED_NAMESPACE = uuid.UUID("84dd784a-8f53-4afc-8e47-8a8cb35d79f4")


def _stable_uuid(label: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, label)


def upgrade() -> None:
    bind = op.get_bind()

    # Disable daily refill behavior: this tier should spend from a fixed pool only.
    bind.execute(
        sa.text(
            """
            UPDATE subscription_tier
            SET daily_image_energy = 0
            WHERE id = :tier_id
            """
        ),
        {"tier_id": WELCOMING_BONUS_TIER_ID},
    )

    # Ensure a fixed image credit pool exists per model.
    for model_name in IMAGE_MODELS:
        bind.execute(
            sa.text(
                """
                INSERT INTO tier_image_model_limit (id, tier_id, image_model, monthly_requests)
                VALUES (:id, :tier_id, :image_model, :monthly_requests)
                ON CONFLICT (tier_id, image_model)
                DO UPDATE SET monthly_requests = EXCLUDED.monthly_requests
                """
            ),
            {
                "id": _stable_uuid(f"welcoming-bonus:model:{model_name}"),
                "tier_id": WELCOMING_BONUS_TIER_ID,
                "image_model": model_name,
                "monthly_requests": FIXED_CREDITS_PER_MODEL,
            },
        )

    # Allow all qualities for the welcoming tier.
    for quality in IMAGE_QUALITIES:
        bind.execute(
            sa.text(
                """
                INSERT INTO tier_image_quality_limit (id, tier_id, quality)
                VALUES (:id, :tier_id, :quality)
                ON CONFLICT (tier_id, quality) DO NOTHING
                """
            ),
            {
                "id": _stable_uuid(f"welcoming-bonus:quality:{quality}"),
                "tier_id": WELCOMING_BONUS_TIER_ID,
                "quality": quality,
            },
        )


def downgrade() -> None:
    bind = op.get_bind()

    # Restore previous Welcoming Bonus quality policy (medium only).
    bind.execute(
        sa.text(
            """
            DELETE FROM tier_image_quality_limit
            WHERE tier_id = :tier_id AND quality IN ('low', 'high')
            """
        ),
        {"tier_id": WELCOMING_BONUS_TIER_ID},
    )

    # Restore the previous daily refill behavior value.
    bind.execute(
        sa.text(
            """
            UPDATE subscription_tier
            SET daily_image_energy = 1
            WHERE id = :tier_id
            """
        ),
        {"tier_id": WELCOMING_BONUS_TIER_ID},
    )

    # Restore previous fixed credit cap.
    bind.execute(
        sa.text(
            """
            UPDATE tier_image_model_limit
            SET monthly_requests = 20
            WHERE tier_id = :tier_id
              AND image_model IN ('gpt-image-1.5', 'gpt-image-2')
            """
        ),
        {"tier_id": WELCOMING_BONUS_TIER_ID},
    )
