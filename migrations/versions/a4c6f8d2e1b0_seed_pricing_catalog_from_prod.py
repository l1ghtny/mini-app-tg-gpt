"""seed pricing catalog from prod

Revision ID: a4c6f8d2e1b0
Revises: 9e4afff3d0fc
Create Date: 2026-04-02 20:15:00.000000

This migration backfills the current production pricing catalog into the newer
schema introduced after prod head 74b7a1f39ae2. It preserves existing tier rows
by upserting on tier name, seeds text limits and image quality pricing from the
live dump, and maps legacy generic image quotas onto the new per-image-model and
per-quality tables using the launch allow-lists defined for each tier.
"""

from __future__ import annotations

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert


# revision identifiers, used by Alembic.
revision: str = "a4c6f8d2e1b0"
down_revision: Union[str, Sequence[str], None] = "9e4afff3d0fc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SEED_NAMESPACE = uuid.UUID("d65d2d8b-e763-4a65-a8a6-9f22e0cbe13c")
IMAGE_MODEL = "gpt-image-1.5"
IMAGE_QUALITIES = ("low", "medium", "high")

TIER_SEEDS = (
    {
        "id": uuid.UUID("9cf6d44a-103f-433e-8c28-8b6e4fa527aa"),
        "name": "Katush Tier",
        "name_ru": "\u041a\u0430\u0442\u044e\u0448 \u0442\u0430\u0440\u0438\u0444",
        "description": "For my wife",
        "description_ru": "\u0414\u043b\u044f \u0416\u0435\u043d\u044b",
        "price_cents": 0,
        "monthly_images": 1000,
        "monthly_docs": 0,
        "monthly_deepsearch": 0,
        "is_active": True,
        "is_public": False,
        "index": 0,
        "is_recurring": True,
    },
    {
        "id": uuid.UUID("0dcb674e-5b4f-4d46-bb08-fb6f2fd49f85"),
        "name": "Close Friends Tier",
        "name_ru": "\u0422\u0430\u0440\u0438\u0444 \u0434\u043b\u044f \u0434\u0440\u0443\u0437\u0435\u0439",
        "description": "For my friends",
        "description_ru": "\u0414\u043b\u044f \u0441\u0432\u043e\u0438\u0445",
        "price_cents": 0,
        "monthly_images": 100,
        "monthly_docs": 0,
        "monthly_deepsearch": 0,
        "is_active": True,
        "is_public": False,
        "index": 0,
        "is_recurring": True,
    },
    {
        "id": uuid.UUID("ba91cb2b-b5af-4f3e-9e28-4c7315f9b557"),
        "name": "Beta Test",
        "name_ru": "\u0411\u0435\u0442\u0430 \u0422\u0435\u0441\u0442",
        "description": "Public Beta Test",
        "description_ru": "\u041f\u0443\u0431\u043b\u0438\u0447\u043d\u044b\u0439 \u0411\u0435\u0442\u0430 \u0422\u0435\u0441\u0442",
        "price_cents": 0,
        "monthly_images": 30,
        "monthly_docs": 0,
        "monthly_deepsearch": 0,
        "is_active": True,
        "is_public": False,
        "index": 0,
        "is_recurring": True,
    },
    {
        "id": uuid.UUID("9be510e0-a666-47a4-8870-f5151bdc15c6"),
        "name": "Beta Access",
        "name_ru": "\u0411\u0435\u0442\u0430 \u0414\u043e\u0441\u0442\u0443\u043f",
        "description": "Beta test for two people",
        "description_ru": "\u0411\u0435\u0442\u0430 \u0434\u043e\u0441\u0442\u0443\u043f \u0437\u0430\u043a\u0440\u044b\u0442\u044b\u0439",
        "price_cents": 0,
        "monthly_images": 20,
        "monthly_docs": 0,
        "monthly_deepsearch": 0,
        "is_active": True,
        "is_public": False,
        "index": 0,
        "is_recurring": True,
    },
    {
        "id": uuid.UUID("f27ac6fb-70d8-4d01-9f44-7ae663e4b322"),
        "name": "Bank Test",
        "name_ru": None,
        "description": None,
        "description_ru": None,
        "price_cents": 0,
        "monthly_images": 2,
        "monthly_docs": 0,
        "monthly_deepsearch": 0,
        "is_active": True,
        "is_public": False,
        "index": 0,
        "is_recurring": True,
    },
    {
        "id": uuid.UUID("f5917068-e548-48d3-bffd-ba499915512e"),
        "name": "Advanced",
        "name_ru": "\u041f\u0440\u043e\u0434\u0432\u0438\u043d\u0443\u0442\u044b\u0439",
        "description": "Average Tier",
        "description_ru": "\u0421\u0440\u0435\u0434\u043d\u0438\u0439 \u0443\u0440\u043e\u0432\u0435\u043d\u044c \u0441 \u0431\u043e\u043b\u044c\u0448\u0438\u043c \u043a\u043e\u043b\u0438\u0447\u0435\u0441\u0442\u0432\u043e\u043c \u0437\u0430\u043f\u0440\u043e\u0441\u043e\u0432 \u043a \u0440\u0430\u0437\u043d\u044b\u043c \u043c\u043e\u0434\u0435\u043b\u044f\u043c",
        "price_cents": 1490,
        "monthly_images": 100,
        "monthly_docs": 0,
        "monthly_deepsearch": 0,
        "is_active": True,
        "is_public": True,
        "index": 2,
        "is_recurring": True,
    },
    {
        "id": uuid.UUID("f94de862-776f-45ac-923f-eea03182a054"),
        "name": "Premium",
        "name_ru": "\u041f\u0440\u0435\u043c\u0438\u0443\u043c",
        "description": "Best Tier",
        "description_ru": "\u0421\u0430\u043c\u044b\u0439 \u0432\u044b\u0441\u043e\u043a\u0438\u0439 \u0443\u0440\u043e\u0432\u0435\u043d\u044c \u043f\u043e\u0434\u043f\u0438\u0441\u043a\u0438 \u0441 \u0431\u043e\u043b\u044c\u0448\u0438\u043c\u0438 \u043b\u0438\u043c\u0438\u0442\u0430\u043c\u0438",
        "price_cents": 2190,
        "monthly_images": 300,
        "monthly_docs": 0,
        "monthly_deepsearch": 0,
        "is_active": True,
        "is_public": True,
        "index": 3,
        "is_recurring": True,
    },
    {
        "id": uuid.UUID("716c8ac9-fd20-4767-b41e-ee2a4d323b05"),
        "name": "Basic",
        "name_ru": "\u0411\u0430\u0437\u043e\u0432\u044b\u0439",
        "description": "Basic Tier",
        "description_ru": "\u041d\u0435\u0434\u043e\u0440\u043e\u0433\u043e\u0439 \u0442\u0430\u0440\u0438\u0444 \u0434\u043b\u044f \u0442\u0435\u0445, \u043a\u0442\u043e \u043d\u0435 \u043e\u0447\u0435\u043d\u044c \u0430\u043a\u0442\u0438\u0432\u043d\u043e \u043f\u043e\u043b\u044c\u0437\u0443\u0435\u0442\u0441\u044f \u0438\u043b\u0438 \u043d\u0435 \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0435\u0442 \u0434\u043b\u044f \u0440\u0435\u0448\u0435\u043d\u0438\u044f \u0435\u0436\u0435\u0434\u043d\u0435\u0432\u043d\u044b\u0445 \u0437\u0430\u0434\u0430\u0447",
        "price_cents": 490,
        "monthly_images": 25,
        "monthly_docs": 0,
        "monthly_deepsearch": 0,
        "is_active": True,
        "is_public": True,
        "index": 1,
        "is_recurring": True,
    },
    {
        "id": uuid.UUID("2f9c27a3-d22e-4693-9c1b-94554a56b1e3"),
        "name": "Welcoming Bonus",
        "name_ru": "\u041f\u0440\u0438\u0432\u0435\u0442\u0441\u0442\u0432\u0435\u043d\u043d\u044b\u0439 \u0431\u043e\u043d\u0443\u0441",
        "description": "Bonus requests for new users!",
        "description_ru": "\u0411\u043e\u043d\u0443\u0441\u043d\u044b\u0435 \u0437\u0430\u043f\u0440\u043e\u0441\u044b \u0434\u043b\u044f \u043d\u043e\u0432\u044b\u0445 \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u0435\u0439",
        "price_cents": 0,
        "monthly_images": 2,
        "monthly_docs": 0,
        "monthly_deepsearch": 0,
        "is_active": True,
        "is_public": False,
        "index": 0,
        "is_recurring": False,
    },
    {
        "id": uuid.UUID("85deb0e7-9f8a-4737-8693-474529f752c9"),
        "name": "Free",
        "name_ru": "\u0411\u0435\u0441\u043f\u043b\u0430\u0442\u043d\u044b\u0439",
        "description": "Free Tier",
        "description_ru": "\u0411\u0435\u0441\u043f\u043b\u0430\u0442\u043d\u044b\u0439 \u0442\u0430\u0440\u0438\u0444",
        "price_cents": 0,
        "monthly_images": 20,
        "monthly_docs": 0,
        "monthly_deepsearch": 0,
        "is_active": True,
        "is_public": False,
        "index": 0,
        "is_recurring": True,
    },
)

TEXT_LIMITS = {
    "Katush Tier": {"gpt-5.2": 1000, "gpt-5-mini": 1000, "gpt-5-nano": 1000},
    "Close Friends Tier": {"gpt-5.2": 300, "gpt-5-mini": 1000, "gpt-5-nano": 5000},
    "Beta Test": {"gpt-5.2": 30, "gpt-5-mini": 100, "gpt-5-nano": 500},
    "Beta Access": {"gpt-5.2": 20, "gpt-5-mini": 200, "gpt-5-nano": 500},
    "Bank Test": {"gpt-5.2": 10, "gpt-5-mini": 10, "gpt-5-nano": 10},
    "Advanced": {"gpt-5.2": 350, "gpt-5-mini": 700, "gpt-5-nano": 3000},
    "Premium": {"gpt-5.2": 900, "gpt-5-mini": 1500, "gpt-5-nano": 4000},
    "Basic": {"gpt-5.2": 10, "gpt-5-mini": 500, "gpt-5-nano": 1500},
    "Welcoming Bonus": {"gpt-5.2": 1, "gpt-5-mini": 5, "gpt-5-nano": 100},
    "Free": {"gpt-5.2": 5, "gpt-5-mini": 50, "gpt-5-nano": 200},
}

IMAGE_QUALITY_PRICING = (
    {"image_model": IMAGE_MODEL, "quality": "low", "credit_cost": 1.0, "description": None, "is_active": True},
    {"image_model": IMAGE_MODEL, "quality": "medium", "credit_cost": 3.0, "description": None, "is_active": True},
    {"image_model": IMAGE_MODEL, "quality": "high", "credit_cost": 9.0, "description": None, "is_active": True},
)

QUALITY_ALLOWLISTS = {
    "Close Friends Tier": ("low", "medium"),
    "Welcoming Bonus": ("medium",),
    "Basic": ("low",),
    "Advanced": ("low", "medium"),
    "Premium": ("low", "medium", "high"),
}


def _daily_limit_for_monthly_images(monthly_images: int) -> int:
    if monthly_images <= 0:
        return 0
    return max(1, (monthly_images + 29) // 30)


def _stable_uuid(label: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, label)


def upgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()

    subscription_tier = sa.Table(
        "subscription_tier",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String()),
        sa.Column("name_ru", sa.String()),
        sa.Column("description", sa.String()),
        sa.Column("description_ru", sa.String()),
        sa.Column("price_cents", sa.Integer()),
        sa.Column("monthly_images", sa.Integer()),
        sa.Column("daily_image_limit", sa.Integer()),
        sa.Column("monthly_docs", sa.Integer()),
        sa.Column("monthly_deepsearch", sa.Integer()),
        sa.Column("is_active", sa.Boolean()),
        sa.Column("is_public", sa.Boolean()),
        sa.Column("index", sa.Integer()),
        sa.Column("is_recurring", sa.Boolean()),
    )
    tier_model_limit = sa.Table(
        "tier_model_limit",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tier_id", sa.Uuid()),
        sa.Column("model_name", sa.String()),
        sa.Column("monthly_requests", sa.Integer()),
    )
    tier_image_model_limit = sa.Table(
        "tier_image_model_limit",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tier_id", sa.Uuid()),
        sa.Column("image_model", sa.String()),
        sa.Column("monthly_requests", sa.Integer()),
    )
    tier_image_quality_limit = sa.Table(
        "tier_image_quality_limit",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tier_id", sa.Uuid()),
        sa.Column("quality", sa.String()),
    )
    image_quality_pricing = sa.Table(
        "image_quality_pricing",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("image_model", sa.String()),
        sa.Column("quality", sa.String()),
        sa.Column("credit_cost", sa.Float()),
        sa.Column("description", sa.String()),
        sa.Column("is_active", sa.Boolean()),
    )

    for seed in TIER_SEEDS:
        values = {
            **seed,
            "daily_image_limit": _daily_limit_for_monthly_images(seed["monthly_images"]),
        }
        insert_stmt = pg_insert(subscription_tier).values(values)
        bind.execute(
            insert_stmt.on_conflict_do_update(
                index_elements=[subscription_tier.c.name],
                set_={
                    "name_ru": insert_stmt.excluded.name_ru,
                    "description": insert_stmt.excluded.description,
                    "description_ru": insert_stmt.excluded.description_ru,
                    "price_cents": insert_stmt.excluded.price_cents,
                    "monthly_images": insert_stmt.excluded.monthly_images,
                    "daily_image_limit": insert_stmt.excluded.daily_image_limit,
                    "monthly_docs": insert_stmt.excluded.monthly_docs,
                    "monthly_deepsearch": insert_stmt.excluded.monthly_deepsearch,
                    "is_active": insert_stmt.excluded.is_active,
                    "is_public": insert_stmt.excluded.is_public,
                    "index": insert_stmt.excluded.index,
                    "is_recurring": insert_stmt.excluded.is_recurring,
                },
            )
        )

    tier_rows = bind.execute(
        sa.select(
            subscription_tier.c.id,
            subscription_tier.c.name,
            subscription_tier.c.monthly_images,
            subscription_tier.c.is_active,
        ).where(subscription_tier.c.name.in_([seed["name"] for seed in TIER_SEEDS]))
    ).all()
    tier_by_name = {row.name: row for row in tier_rows}

    for tier_name, limits in TEXT_LIMITS.items():
        tier_row = tier_by_name.get(tier_name)
        if not tier_row:
            continue
        for model_name, monthly_requests in limits.items():
            insert_stmt = pg_insert(tier_model_limit).values(
                id=_stable_uuid(f"tier-model:{tier_name}:{model_name}"),
                tier_id=tier_row.id,
                model_name=model_name,
                monthly_requests=monthly_requests,
            )
            bind.execute(
                insert_stmt.on_conflict_do_update(
                    index_elements=[tier_model_limit.c.tier_id, tier_model_limit.c.model_name],
                    set_={"monthly_requests": insert_stmt.excluded.monthly_requests},
                )
            )

    for pricing in IMAGE_QUALITY_PRICING:
        existing = bind.execute(
            sa.select(image_quality_pricing.c.id).where(
                image_quality_pricing.c.image_model == pricing["image_model"],
                image_quality_pricing.c.quality == pricing["quality"],
            )
        ).first()
        if existing:
            bind.execute(
                image_quality_pricing.update()
                .where(image_quality_pricing.c.id == existing.id)
                .values(
                    credit_cost=pricing["credit_cost"],
                    description=pricing["description"],
                    is_active=pricing["is_active"],
                )
            )
        else:
            bind.execute(
                image_quality_pricing.insert().values(
                    id=_stable_uuid(f"image-quality:{pricing['image_model']}:{pricing['quality']}"),
                    **pricing,
                )
            )

    for tier_name, tier_row in tier_by_name.items():
        if not tier_row.is_active or (tier_row.monthly_images or 0) <= 0:
            continue

        insert_stmt = pg_insert(tier_image_model_limit).values(
            id=_stable_uuid(f"tier-image-model:{tier_name}:{IMAGE_MODEL}"),
            tier_id=tier_row.id,
            image_model=IMAGE_MODEL,
            monthly_requests=tier_row.monthly_images,
        )
        bind.execute(
            insert_stmt.on_conflict_do_update(
                index_elements=[tier_image_model_limit.c.tier_id, tier_image_model_limit.c.image_model],
                set_={"monthly_requests": insert_stmt.excluded.monthly_requests},
            )
        )

        allowed_qualities = QUALITY_ALLOWLISTS.get(tier_name, IMAGE_QUALITIES)

        bind.execute(
            tier_image_quality_limit.delete().where(
                tier_image_quality_limit.c.tier_id == tier_row.id,
                tier_image_quality_limit.c.quality.in_(IMAGE_QUALITIES),
            )
        )

        for quality in allowed_qualities:
            insert_stmt = pg_insert(tier_image_quality_limit).values(
                id=_stable_uuid(f"tier-image-quality:{tier_name}:{quality}"),
                tier_id=tier_row.id,
                quality=quality,
            )
            bind.execute(
                insert_stmt.on_conflict_do_nothing(
                    index_elements=[tier_image_quality_limit.c.tier_id, tier_image_quality_limit.c.quality]
                )
            )


def downgrade() -> None:
    """Irreversible seed migration."""
    pass
