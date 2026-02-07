import math
import uuid

from sqlmodel import select, func
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import ImageQualityPricing, RequestLedger
from app.db.subscription_tiers import TierImageModelLimit, TierImageQualityLimit
from app.schemas.usage import FeatureUsageResponse, UserImageUsageResponse, UserTextUsageResponse
from app.services.subscription_check.entitlements import (
    get_active_tier,
    get_active_subscriptions,
    get_active_usage_packs,
    list_image_entitlements,
    list_text_entitlements,
    remaining_images,
)
from app.services.subscription_check.pacing import check_image_pacing


async def get_text_usage(session: AsyncSession, user) -> UserTextUsageResponse:
    subscriptions = await get_active_subscriptions(session, user.id)
    packs = await get_active_usage_packs(session, user.id)
    if not subscriptions and not packs:
        return UserTextUsageResponse(status="none", models=[])

    model_names: set[str] = set()
    for sub in subscriptions:
        for limit in sub.tier.tier_model_limits:
            model_names.add(limit.model_name)
    for pack in packs:
        for limit in pack.pack.pack_model_limits:
            model_names.add(limit.model_name)

    models = []
    for model_name in sorted(model_names):
        ent = await list_text_entitlements(session, user.id, model_name)
        models.append({
            "model": model_name,
            "total_remaining": ent["total_remaining"],
            "selected": ent["selected"],
            "entitlements": ent["entitlements"],
        })

    return UserTextUsageResponse(status="active", models=models)


async def get_feature_usage(session: AsyncSession, user) -> FeatureUsageResponse:
    tier = await get_active_tier(session, user.id)
    if not tier:
        return FeatureUsageResponse(status="none", features={})

    start = func.date_trunc("month", func.now())

    img_cap = tier.monthly_images or 0
    img_used = (await session.exec(
        select(func.count()).where(
            RequestLedger.user_id == user.id,
            (RequestLedger.tier_id == tier.id)
            | ((RequestLedger.tier_id.is_(None)) & (RequestLedger.usage_pack_id.is_(None))),
            RequestLedger.feature == "image",
            RequestLedger.state.in_(("reserved", "consumed")),
            RequestLedger.created_at >= start,
        )
    )).one() or 0

    img_remaining = await remaining_images(session, user.id, tier)

    return FeatureUsageResponse(
        status="active",
        features={
            "images": {"cap": img_cap, "used": img_used, "remaining": img_remaining},
        },
    )


async def get_image_usage(session: AsyncSession, user) -> UserImageUsageResponse:
    subscriptions = await get_active_subscriptions(session, user.id)
    packs = await get_active_usage_packs(session, user.id)
    if not subscriptions and not packs:
        return UserImageUsageResponse(status="none", models=[])

    image_models: set[str] = set()
    for sub in subscriptions:
        for limit in sub.tier.tier_image_model_limits:
            image_models.add(limit.image_model)
    for pack in packs:
        for limit in pack.pack.pack_image_model_limits:
            image_models.add(limit.image_model)

    if not image_models:
        return UserImageUsageResponse(status="active", models=[])

    quality_allowed_by_model: dict[TierImageModelLimit.image_model, list[TierImageQualityLimit.quality]]


    pricing_rows = (await session.exec(
        select(ImageQualityPricing)
        .where(
            ImageQualityPricing.image_model.in_(image_models),
            ImageQualityPricing.is_active == True,
        )
    )).all()

    pricing_by_model: dict[str, list[ImageQualityPricing]] = {}
    for row in pricing_rows:
        pricing_by_model.setdefault(row.image_model, []).append(row)

    models = []
    for image_model in sorted(image_models):
        breakdown = await list_image_entitlements(session, user.id, image_model)
        entitlements = breakdown["entitlements"]
        total_remaining_credits = breakdown["total_remaining_credits"]

        if image_model == any(pack.pack_image_model_limits for pack in packs):
            quality_allowed_by_model = {{image_model}: ['low', 'medium', 'high']}
        else:
            for ent in entitlements:
                if ent["kind"] == "tier":
                    quality_allowed_by_model = {image_model: ent["allowed_image_qualities"]}

        qualities = []
        for pricing in sorted(pricing_by_model.get(image_model, []), key=lambda p: p.quality):
            if pricing.quality not in quality_allowed_by_model[image_model]:
                continue

            cost = pricing.credit_cost or 1.0
            remaining = int(math.floor(total_remaining_credits / cost)) if cost > 0 else 0

            sources = []
            for ent in entitlements:
                ent_remaining_credits = ent["remaining_credits"]
                ent_remaining = int(math.floor(ent_remaining_credits / cost)) if cost > 0 else 0
                pacing = None
                if ent["kind"] == "tier" and ent["source"] == "subscription":
                    daily_credits = ent.get("daily_image_limit") or 0
                    daily_target = daily_credits if daily_credits > 0 else 4.0
                    tier_id = uuid.UUID(ent["tier_id"]) if ent.get("tier_id") else None
                    if tier_id:
                        is_throttled, wait_time = await check_image_pacing(
                            session,
                            user.id,
                            daily_target=daily_target,
                            cost=cost,
                            tier_id=tier_id,
                        )
                        pacing = {
                            "is_throttled": is_throttled,
                            "wait_seconds": int(wait_time.total_seconds()),
                        }

                sources.append({
                    "kind": ent["kind"],
                    "source": ent["source"],
                    "tier_id": ent.get("tier_id"),
                    "usage_pack_id": ent.get("usage_pack_id"),
                    "cap": ent.get("cap"),
                    "used": ent.get("used"),
                    "remaining": max(0, ent_remaining),
                    "remaining_credits": ent_remaining_credits,
                    "pacing": pacing,
                })

            qualities.append({
                "quality": pricing.quality,
                "credit_cost": pricing.credit_cost,
                "description": pricing.description,
                "remaining": max(0, remaining),
                "remaining_credits": total_remaining_credits,
                "sources": sources,
            })

        models.append({
            "model": image_model,
            "entitlements": entitlements,
            "total_remaining_credits": total_remaining_credits,
            "qualities": qualities,
        })

    return UserImageUsageResponse(status="active", models=models)
