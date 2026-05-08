"""rebalance tiers and unified energy

Revision ID: f2b1c2d3e4f5
Revises: e6f5d4c3b2a1
Create Date: 2026-05-08 14:00:00.000000
"""

from __future__ import annotations

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f2b1c2d3e4f5"
down_revision: Union[str, Sequence[str], None] = "e6f5d4c3b2a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SEED_NAMESPACE = uuid.UUID("29eb8e8b-8ce4-4f4f-a13e-1a4b99ddd8dd")
MODEL_NAME = "gpt-5.5"
QUALITIES = ("low", "medium", "high")

# Canonical tier names + backwards-compatible aliases for older datasets.
TIER_ALIASES: dict[str, tuple[str, ...]] = {
    "basic": ("basic",),
    "advanced": ("advanced", "pro"),
    "premium": ("premium",),
    "bonus": ("welcoming bonus", "bonus"),
}


def _stable_uuid(label: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, label)


def _find_tier_id(bind: sa.Connection, aliases: tuple[str, ...]) -> uuid.UUID | None:
    for name in aliases:
        row = bind.execute(
            sa.text(
                """
                SELECT id
                FROM subscription_tier
                WHERE lower(name) = :name
                LIMIT 1
                """
            ),
            {"name": name.lower()},
        ).first()
        if row and row.id:
            return row.id
    return None


def _upsert_tier_model_limit(
    bind: sa.Connection,
    *,
    tier_id: uuid.UUID,
    model_name: str,
    monthly_requests: int,
) -> None:
    bind.execute(
        sa.text(
            """
            INSERT INTO tier_model_limit (id, tier_id, model_name, monthly_requests)
            VALUES (:id, :tier_id, :model_name, :monthly_requests)
            ON CONFLICT (tier_id, model_name)
            DO UPDATE SET monthly_requests = EXCLUDED.monthly_requests
            """
        ),
        {
            "id": _stable_uuid(f"tier-model:{tier_id}:{model_name}:f2b1c2d3e4f5"),
            "tier_id": tier_id,
            "model_name": model_name,
            "monthly_requests": monthly_requests,
        },
    )


def _ensure_quality(bind: sa.Connection, *, tier_id: uuid.UUID, quality: str) -> None:
    bind.execute(
        sa.text(
            """
            INSERT INTO tier_image_quality_limit (id, tier_id, quality)
            VALUES (:id, :tier_id, :quality)
            ON CONFLICT (tier_id, quality) DO NOTHING
            """
        ),
        {
            "id": _stable_uuid(f"tier-quality:{tier_id}:{quality}:f2b1c2d3e4f5"),
            "tier_id": tier_id,
            "quality": quality,
        },
    )


def upgrade() -> None:
    bind = op.get_bind()

    basic_id = _find_tier_id(bind, TIER_ALIASES["basic"])
    advanced_id = _find_tier_id(bind, TIER_ALIASES["advanced"])
    premium_id = _find_tier_id(bind, TIER_ALIASES["premium"])
    bonus_id = _find_tier_id(bind, TIER_ALIASES["bonus"])

    # 1) GPT-5.5 rebalance.
    if basic_id is not None:
        _upsert_tier_model_limit(bind, tier_id=basic_id, model_name=MODEL_NAME, monthly_requests=15)
    if advanced_id is not None:
        _upsert_tier_model_limit(bind, tier_id=advanced_id, model_name=MODEL_NAME, monthly_requests=25)
    if bonus_id is not None:
        _upsert_tier_model_limit(bind, tier_id=bonus_id, model_name=MODEL_NAME, monthly_requests=5)

    # 2) Welcoming Bonus fixed one-time pool (no daily refill).
    if bonus_id is not None:
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
            {"tier_id": bonus_id},
        )

    # 3) All qualities available across image-enabled public tiers.
    for tier_id in (basic_id, advanced_id, premium_id, bonus_id):
        if tier_id is None:
            continue
        for quality in QUALITIES:
            _ensure_quality(bind, tier_id=tier_id, quality=quality)


def downgrade() -> None:
    bind = op.get_bind()

    basic_id = _find_tier_id(bind, TIER_ALIASES["basic"])
    advanced_id = _find_tier_id(bind, TIER_ALIASES["advanced"])
    bonus_id = _find_tier_id(bind, TIER_ALIASES["bonus"])

    # Best-effort rollback to the previous baseline used before this rebalance.
    if basic_id is not None:
        _upsert_tier_model_limit(bind, tier_id=basic_id, model_name=MODEL_NAME, monthly_requests=0)
    if advanced_id is not None:
        _upsert_tier_model_limit(bind, tier_id=advanced_id, model_name=MODEL_NAME, monthly_requests=0)
    if bonus_id is not None:
        _upsert_tier_model_limit(bind, tier_id=bonus_id, model_name=MODEL_NAME, monthly_requests=1)
        bind.execute(
            sa.text(
                """
                UPDATE subscription_tier
                SET monthly_images = 0,
                    daily_image_energy = 3,
                    is_recurring = true
                WHERE id = :tier_id
                """
            ),
            {"tier_id": bonus_id},
        )
