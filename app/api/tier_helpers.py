import uuid
import re

from fastapi import HTTPException
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import ImageQualityPricing
from app.db.subscription_tiers import SubscriptionTier
from app.schemas.subscriptions import (
    ImageQualityPricingResponse,
    SubscriptionTierResponse,
    TierImageModelLimits,
    TierMonthlyLimits,
    TierSubscribeResponse,
)
from app.services.subscription_check.realtime_check import check_tier


def _tier_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "tier"


async def list_public_tiers(session: AsyncSession, user) -> list[SubscriptionTierResponse]:
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    tiers_info = (await session.exec(
        select(SubscriptionTier)
        .where(SubscriptionTier.is_public == True)
        .order_by(SubscriptionTier.index)
        .options(
            selectinload(SubscriptionTier.tier_model_limits),
            selectinload(SubscriptionTier.tier_image_model_limits),
            selectinload(SubscriptionTier.tier_image_quality_limits),
        )
    )).all()

    if not tiers_info:
        raise HTTPException(status_code=404, detail="No tiers found")

    current_tier = await check_tier(user, session)
    if current_tier and all(tier.id != current_tier.id for tier in tiers_info):
        loaded_tier = await _load_tier_with_limits(session, current_tier.id)
        if loaded_tier:
            tiers_info.insert(0, loaded_tier)

    pricing_by_model = await _load_image_pricing(session)
    return [_build_tier_response(tier, pricing_by_model) for tier in tiers_info]


async def get_tier_detail(
    session: AsyncSession,
    user,
    tier_id: uuid.UUID,
) -> SubscriptionTierResponse:
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    tier_info = await _load_tier_with_limits(session, tier_id)

    if not tier_info:
        raise HTTPException(status_code=404, detail="Tier not found")

    pricing_by_model = await _load_image_pricing(session)
    return _build_tier_response(tier_info, pricing_by_model)


async def subscribe_to_tier(
    session: AsyncSession,
    user,
    tier_id: uuid.UUID,
) -> TierSubscribeResponse:
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    tier = await session.get(SubscriptionTier, tier_id)
    if not tier:
        raise HTTPException(status_code=404, detail="Tier not found")

    return TierSubscribeResponse(status="ok", tier_id=str(tier.id))


async def _load_tier_with_limits(
    session: AsyncSession,
    tier_id: uuid.UUID,
) -> SubscriptionTier | None:
    return (await session.exec(
        select(SubscriptionTier)
        .where(SubscriptionTier.id == tier_id)
        .options(
            selectinload(SubscriptionTier.tier_model_limits),
            selectinload(SubscriptionTier.tier_image_model_limits),
            selectinload(SubscriptionTier.tier_image_quality_limits),
        )
    )).first()


async def _load_image_pricing(session: AsyncSession) -> dict[str, list[ImageQualityPricing]]:
    pricing_rows = (await session.exec(
        select(ImageQualityPricing).where(ImageQualityPricing.is_active == True)
    )).all()

    pricing_by_model: dict[str, list[ImageQualityPricing]] = {}
    for row in pricing_rows:
        pricing_by_model.setdefault(row.image_model, []).append(row)
    return pricing_by_model


def _build_tier_response(
    tier: SubscriptionTier,
    pricing_by_model: dict[str, list[ImageQualityPricing]],
) -> SubscriptionTierResponse:
    image_limit_override = -1 if (tier.daily_image_limit or 0) > 0 else None
    allowed_models = sorted({l.image_model for l in tier.tier_image_model_limits})
    allowed_qualities = sorted({l.quality for l in tier.tier_image_quality_limits})
    allowed_quality_set = set(allowed_qualities)
    image_pricing: list[ImageQualityPricingResponse] = []
    for image_model in allowed_models:
        for pricing in sorted(pricing_by_model.get(image_model, []), key=lambda p: p.quality):
            if allowed_quality_set and pricing.quality not in allowed_quality_set:
                continue
            image_pricing.append(ImageQualityPricingResponse(
                image_model=pricing.image_model,
                quality=pricing.quality,
                credit_cost=pricing.credit_cost,
                description=pricing.description,
            ))

    return SubscriptionTierResponse(
        name=tier.name,
        name_ru=tier.name_ru,
        slug=_tier_slug(tier.name),
        rank=tier.index or 0,
        description=tier.description,
        description_ru=tier.description_ru,
        price_cents=tier.price_cents,
        monthly_images=tier.monthly_images,
        tier_model_limits=[
            TierMonthlyLimits(model_name=l.model_name, requests_limit=l.monthly_requests)
            for l in tier.tier_model_limits
        ],
        tier_image_model_limits=[
            TierImageModelLimits(
                image_model=l.image_model,
                requests_limit=image_limit_override if image_limit_override is not None else l.monthly_requests,
            )
            for l in tier.tier_image_model_limits
        ],
        image_quality_pricing=image_pricing,
        is_recurring=tier.is_recurring,
        daily_image_limit=tier.daily_image_limit,
        allowed_image_qualities=allowed_qualities,
        allowed_image_models=allowed_models,
        tier_id=str(tier.id),
    )
