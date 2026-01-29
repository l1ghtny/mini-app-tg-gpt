import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.dependencies import get_current_user
from app.core.config import settings
from app.db.database import get_session
from app.db.models import ImageQualityPricing
from app.db.subscription_tiers import SubscriptionTier
from app.schemas.subscriptions import (
    SubscriptionTierResponse,
    TierMonthlyLimits,
    TierImageModelLimits,
    ImageQualityPricingResponse,
    TierSubscribeResponse,
)
from app.services.subscription_check.realtime_check import check_tier

tiers = APIRouter(tags=['subscription tiers'], prefix='/tiers')



@tiers.get("", response_model=List[SubscriptionTierResponse])
async def get_tiers(
        user = Depends(get_current_user),
        session: AsyncSession = Depends(get_session)
    ):

    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    tiers_info = (await session.exec(
        select(SubscriptionTier)
        .where(SubscriptionTier.is_public==True)
        .order_by(SubscriptionTier.index)
        .options(
            selectinload(SubscriptionTier.tier_model_limits),
            selectinload(SubscriptionTier.tier_image_model_limits),
        )
    )).all()

    if not tiers_info:
        raise HTTPException(status_code=404, detail="No tiers found")

    current_tier = await check_tier(user, session)
    if current_tier and current_tier not in tiers_info:
        tiers_info.insert(0, current_tier)


    pricing_rows = (await session.exec(
        select(ImageQualityPricing).where(ImageQualityPricing.is_active == True)
    )).all()
    pricing_by_model: dict[str, list[ImageQualityPricing]] = {}
    for row in pricing_rows:
        pricing_by_model.setdefault(row.image_model, []).append(row)

    tiers_enriched = []
    for tier in tiers_info:
        image_models = {l.image_model for l in tier.tier_image_model_limits}
        image_pricing = []
        for image_model in sorted(image_models):
            for pricing in sorted(pricing_by_model.get(image_model, []), key=lambda p: p.quality):
                image_pricing.append(ImageQualityPricingResponse(
                    image_model=pricing.image_model,
                    quality=pricing.quality,
                    credit_cost=pricing.credit_cost,
                    description=pricing.description,
                ))
        tiers_enriched.append(SubscriptionTierResponse(
            name=tier.name,
            name_ru=tier.name_ru,
            description=tier.description,
            description_ru=tier.description_ru,
            price_cents=tier.price_cents,
            monthly_images=tier.monthly_images,
            tier_model_limits=[
                TierMonthlyLimits(model_name=l.model_name, requests_limit=l.monthly_requests)
                for l in tier.tier_model_limits
            ],
            tier_image_model_limits=[
                TierImageModelLimits(image_model=l.image_model, requests_limit=l.monthly_requests)
                for l in tier.tier_image_model_limits
            ],
            image_quality_pricing=image_pricing,
            is_recurring=tier.is_recurring,
            daily_image_limit=tier.daily_image_limit,
            tier_id=str(tier.id),
        ))
    return tiers_enriched



@tiers.get("/{tier_id}", response_model=SubscriptionTierResponse)
async def get_tier(tier_id: uuid.UUID, user = Depends(get_current_user), session: AsyncSession = Depends(get_session)):

    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    tier_info = (await session.exec(
        select(SubscriptionTier)
        .where(SubscriptionTier.id == tier_id)
        .options(
            selectinload(SubscriptionTier.tier_model_limits),
            selectinload(SubscriptionTier.tier_image_model_limits),
        )
    )).first()

    if not tier_info:
        raise HTTPException(status_code=404, detail="Tier not found")

    pricing_rows = (await session.exec(
        select(ImageQualityPricing).where(ImageQualityPricing.is_active == True)
    )).all()
    pricing_by_model: dict[str, list[ImageQualityPricing]] = {}
    for row in pricing_rows:
        pricing_by_model.setdefault(row.image_model, []).append(row)

    image_models = {l.image_model for l in tier_info.tier_image_model_limits}
    image_pricing = []
    for image_model in sorted(image_models):
        for pricing in sorted(pricing_by_model.get(image_model, []), key=lambda p: p.quality):
            image_pricing.append(ImageQualityPricingResponse(
                image_model=pricing.image_model,
                quality=pricing.quality,
                credit_cost=pricing.credit_cost,
                description=pricing.description,
            ))

    tier_enriched = SubscriptionTierResponse(
        name=tier_info.name,
        description=tier_info.description,
        name_ru=tier_info.name_ru,
        description_ru=tier_info.description_ru,
        price_cents=tier_info.price_cents,
        monthly_images=tier_info.monthly_images,
        tier_model_limits=[
            TierMonthlyLimits(model_name=l.model_name, requests_limit=l.monthly_requests)
            for l in tier_info.tier_model_limits
        ],
        tier_image_model_limits=[
            TierImageModelLimits(image_model=l.image_model, requests_limit=l.monthly_requests)
            for l in tier_info.tier_image_model_limits
        ],
        image_quality_pricing=image_pricing,
        is_recurring=tier_info.is_recurring,
        daily_image_limit=tier_info.daily_image_limit,
        tier_id=str(tier_info.id),
    )

    return tier_enriched


@tiers.post('/subscribe/{tier_id}', response_model=TierSubscribeResponse)
async def tier_subscribe(tier_id: uuid.UUID, user = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    tier = await session.get(SubscriptionTier, tier_id)
    if not tier:
        raise HTTPException(status_code=404, detail="Tier not found")

    return TierSubscribeResponse(status="ok", tier_id=str(tier.id))

