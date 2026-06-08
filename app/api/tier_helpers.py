import uuid
import re

from fastapi import HTTPException
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import ImageQualityPricing
from app.db.subscription_tiers import GeneralDiscount, SubscriptionTier
from app.schemas.subscriptions import (
    ImageQualityPricingResponse,
    SubscriptionDiscountResponse,
    SubscriptionTierResponse,
    TierImageModelLimits,
    TierMonthlyLimits,
    TierSubscribeResponse,
)
from app.services.model_registry import get_image_model_provider
from app.services.subscription_check.realtime_check import check_tier


def _tier_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "tier"


def _daily_image_energy(tier: SubscriptionTier) -> int:
    return int(getattr(tier, "daily_image_energy", 0) or 0)


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
    general_discounts = await _load_applicable_general_discounts(session)
    return [_build_tier_response(tier, pricing_by_model, general_discounts) for tier in tiers_info]


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
    general_discounts = await _load_applicable_general_discounts(session)
    return _build_tier_response(tier_info, pricing_by_model, general_discounts)


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
        if get_image_model_provider(row.image_model) == "google" and row.quality not in {"512", "1k", "2k"}:
            continue
        pricing_by_model.setdefault(row.image_model, []).append(row)
    return pricing_by_model


async def _load_applicable_general_discounts(
    session: AsyncSession,
) -> list[SubscriptionDiscountResponse]:
    """Load active GeneralDiscount rows without user-specific eligibility checks.
    Used for the public pricing catalog — conditions are forwarded to frontend.
    """
    from datetime import datetime
    now = datetime.utcnow()
    rows = (await session.exec(
        select(GeneralDiscount).where(
            GeneralDiscount.is_active == True,  # noqa: E712
            (GeneralDiscount.starts_at.is_(None)) | (GeneralDiscount.starts_at <= now),
            (GeneralDiscount.expires_at.is_(None)) | (GeneralDiscount.expires_at > now),
        )
    )).all()

    result: list[SubscriptionDiscountResponse] = []
    for gd in rows:
        applies_to = gd.applies_to_tiers if gd.applies_to_tiers else ["all"]
        result.append(
            SubscriptionDiscountResponse(
                code=gd.code,
                type=gd.type,
                percent_off=gd.percent_off,
                applies_to=applies_to,
                expires_at=gd.expires_at.isoformat(timespec="seconds") if gd.expires_at else None,
                stackable=gd.stackable,
                conditions=gd.conditions if gd.conditions else None,
            )
        )
    return result


def _build_tier_response(
    tier: SubscriptionTier,
    pricing_by_model: dict[str, list[ImageQualityPricing]],
    applicable_discounts: list[SubscriptionDiscountResponse] | None = None,
) -> SubscriptionTierResponse:
    daily_energy = _daily_image_energy(tier)
    image_limit_override = -1 if daily_energy > 0 else None
    allowed_models = sorted({l.image_model for l in tier.tier_image_model_limits})
    allowed_qualities = sorted({l.quality for l in tier.tier_image_quality_limits})
    image_pricing: list[ImageQualityPricingResponse] = []
    for image_model in allowed_models:
        for pricing in sorted(pricing_by_model.get(image_model, []), key=lambda p: p.quality):
            image_pricing.append(ImageQualityPricingResponse(
                image_model=pricing.image_model,
                quality=pricing.quality,
                credit_cost=pricing.credit_cost,
                energy_cost=pricing.credit_cost,
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
        monthly_docs=tier.monthly_docs,
        max_active_docs=int(getattr(tier, "max_active_docs", 0) or 0),
        max_storage_bytes=int(getattr(tier, "max_storage_bytes", 0) or 0),
        max_file_size_bytes=int(getattr(tier, "max_file_size_bytes", 0) or 0),
        max_pinned_docs=int(getattr(tier, "max_pinned_docs", 0) or 0),
        doc_retention_hours=int(getattr(tier, "doc_retention_hours", 24) or 24),
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
        daily_image_energy=daily_energy,
        image_energy_max=daily_energy * 5,
        allowed_image_qualities=allowed_qualities,
        allowed_image_models=allowed_models,
        tier_id=str(tier.id),
        applicable_discounts=applicable_discounts or [],
    )
