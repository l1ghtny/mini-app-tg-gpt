import math
import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import select, func
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import ImageQualityPricing, RequestLedger, TextModelCatalog
from app.schemas.usage import (
    FeatureUsageResponse,
    UserImageEnergyResponse,
    UserImageUsageResponse,
    UserTextUsageResponse,
)
from app.services.subscription_check.entitlements import (
    get_active_tier,
    get_active_subscriptions,
    get_active_usage_packs,
    list_image_entitlements_bulk,
    list_text_entitlements_bulk,
    remaining_images,
)
from app.services.subscription_check.pacing import check_image_pacing, get_image_energy_snapshot
from app.services.model_registry import (
    get_image_model_provider,
    get_text_usage_bucket,
    get_text_usage_bucket_display_names,
    list_text_usage_bucket_models,
)


def _next_utc_midnight() -> datetime:
    now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    return now + timedelta(days=1)


def _daily_energy_from_tier(tier) -> int:
    return int(getattr(tier, "daily_image_energy", 0) or 0)


def _daily_energy_from_entitlement(ent: dict) -> int:
    return int(ent.get("daily_image_energy") or 0)


def _image_option_sort_key(option: str) -> tuple[int, str]:
    normalized = (option or "").strip().lower()
    order = {
        "512": 0,
        "1k": 1,
        "2k": 2,
        "low": 10,
        "medium": 11,
        "high": 12,
    }
    return order.get(normalized, 100), normalized


async def get_text_usage(session: AsyncSession, user) -> UserTextUsageResponse:
    subscriptions = await get_active_subscriptions(session, user.id)
    packs = await get_active_usage_packs(session, user.id)
    if not subscriptions and not packs:
        return UserTextUsageResponse(status="none", models=[])

    model_names: set[str] = set()
    for sub in subscriptions:
        for limit in sub.tier.tier_model_limits:
            model_names.add(get_text_usage_bucket(limit.model_name))
    for pack in packs:
        for limit in pack.pack.pack_model_limits:
            model_names.add(get_text_usage_bucket(limit.model_name))

    sorted_model_names = sorted(model_names)
    bulk_entitlements = await list_text_entitlements_bulk(session, user.id, sorted_model_names)
    catalog_rows = (await session.exec(
        select(TextModelCatalog).where(
            TextModelCatalog.model_name.in_(sorted_model_names),
            TextModelCatalog.is_active == True,
        )
    )).all()
    catalog_by_model = {row.model_name: row for row in catalog_rows}

    models = []
    for model_name in sorted_model_names:
        ent = bulk_entitlements.get(model_name)
        if ent:
            bucket_model = ent.get("bucket_model", model_name)
            catalog_row = catalog_by_model.get(bucket_model)
            fallback_en, fallback_ru = get_text_usage_bucket_display_names(bucket_model)
            models.append({
                "model": bucket_model,
                "display_name": (catalog_row.display_name if catalog_row and catalog_row.display_name else fallback_en),
                "display_name_ru": (
                    catalog_row.display_name_ru if catalog_row and catalog_row.display_name_ru else fallback_ru
                ),
                "bucket_models": ent.get("bucket_models") or list_text_usage_bucket_models(model_name),
                "total_remaining": ent["total_remaining"],
                "next_reset_at": (
                    ent["selected"].get("next_reset_at")
                    if isinstance(ent.get("selected"), dict)
                    else None
                ),
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

    sorted_image_models = sorted(image_models)
    bulk_entitlements = await list_image_entitlements_bulk(session, user.id, sorted_image_models)

    pricing_rows = (await session.exec(
        select(ImageQualityPricing)
        .where(
            ImageQualityPricing.image_model.in_(image_models),
            ImageQualityPricing.is_active == True,
        )
    )).all()

    pricing_by_model: dict[str, list[ImageQualityPricing]] = {}
    for row in pricing_rows:
        if get_image_model_provider(row.image_model) == "google" and row.quality not in {"512", "1k", "2k"}:
            continue
        pricing_by_model.setdefault(row.image_model, []).append(row)

    models = []
    for image_model in sorted_image_models:
        breakdown = bulk_entitlements.get(image_model)
        if not breakdown:
            continue
        entitlements = breakdown["entitlements"]
        total_remaining_credits = breakdown["total_remaining_credits"]

        resolutions = []
        for pricing in sorted(pricing_by_model.get(image_model, []), key=lambda p: _image_option_sort_key(p.quality)):
            cost = pricing.credit_cost or 1.0
            if total_remaining_credits == -1:
                remaining = -1
            else:
                remaining = int(math.floor(total_remaining_credits / cost)) if cost > 0 else 0

            sources = []
            for ent in entitlements:
                ent_remaining_credits = ent["remaining_credits"]
                if ent_remaining_credits == -1:
                    ent_remaining = -1
                else:
                    ent_remaining = int(math.floor(ent_remaining_credits / cost)) if cost > 0 else 0
                pacing = None
                if ent["kind"] == "tier" and _daily_energy_from_entitlement(ent) > 0:
                    daily_target = _daily_energy_from_entitlement(ent)
                    tier_id = uuid.UUID(ent["tier_id"]) if ent.get("tier_id") else None
                    if tier_id:
                        # Find the actual tier to get is_recurring and monthly_images
                        tier = next((s.tier for s in subscriptions if str(s.tier_id) == str(tier_id)), None)
                        is_recurring = getattr(tier, "is_recurring", True) if tier else True
                        total_pool = float(getattr(tier, "monthly_images", 0) or 0) if tier else 0.0
                        
                        is_throttled, wait_time = await check_image_pacing(
                            session,
                            user.id,
                            daily_target=daily_target,
                            cost=cost,
                            tier_id=tier_id,
                            is_recurring=is_recurring,
                            total_pool=total_pool,
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
                    "remaining": ent_remaining if ent_remaining == -1 else max(0, ent_remaining),
                    "remaining_credits": ent_remaining_credits,
                    "pacing": pacing,
                    "next_reset_at": _next_utc_midnight() if ent.get("daily_image_energy") else None,
                })

            resolution = pricing.quality
            resolutions.append({
                "resolution": resolution,
                "credit_cost": pricing.credit_cost,
                "description": pricing.description,
                "remaining": remaining if remaining == -1 else max(0, remaining),
                "remaining_credits": total_remaining_credits,
                "sources": sources,
            })

        models.append({
            "model": image_model,
            "entitlements": entitlements,
            "total_remaining_credits": total_remaining_credits,
            "resolutions": resolutions,
            "next_reset_at": next(
                (
                    _next_utc_midnight()
                    for ent in entitlements
                    if ent.get("kind") == "tier" and _daily_energy_from_entitlement(ent) > 0
                ),
                None,
            ),
        })

    return UserImageUsageResponse(status="active", models=models)


async def get_image_energy_usage(session: AsyncSession, user) -> UserImageEnergyResponse:
    subscriptions = await get_active_subscriptions(session, user.id)
    if not subscriptions:
        return UserImageEnergyResponse(status="none", sources=[])

    pricing_rows = (await session.exec(
        select(ImageQualityPricing).where(ImageQualityPricing.is_active == True)
    )).all()
    pricing_by_model: dict[str, list[ImageQualityPricing]] = {}
    for row in pricing_rows:
        pricing_by_model.setdefault(row.image_model, []).append(row)

    seen_tiers: set[str] = set()
    sources = []
    for sub in subscriptions:
        tier = sub.tier
        daily_energy = _daily_energy_from_tier(tier)
        is_recurring = getattr(tier, "is_recurring", True)
        monthly_images = tier.monthly_images or 0
        if daily_energy <= 0 and (is_recurring or monthly_images <= 0):
            continue
            
        tier_id_str = str(tier.id)
        if tier_id_str in seen_tiers:
            continue
        seen_tiers.add(tier_id_str)

        snapshot = await get_image_energy_snapshot(
            session=session,
            user_id=user.id,
            daily_target=daily_energy,
            cost=0.0,
            tier_id=tier.id,
            is_recurring=is_recurring,
            total_pool=float(monthly_images),
        )
        allowed_models = {limit.image_model for limit in tier.tier_image_model_limits}
        min_cost = None
        for model_name in allowed_models:
            for pricing in pricing_by_model.get(model_name, []):
                cost = float(pricing.credit_cost or 0.0)
                if cost <= 0:
                    continue
                min_cost = cost if min_cost is None else min(min_cost, cost)
        check_cost = min_cost if min_cost is not None else 1.0

        is_throttled, wait_time = await check_image_pacing(
            session=session,
            user_id=user.id,
            daily_target=daily_energy,
            cost=check_cost,
            tier_id=tier.id,
            is_recurring=is_recurring,
            total_pool=float(monthly_images),
        )
        max_energy = int(snapshot.capacity)
        available_energy = int(snapshot.available_energy)
        saved_energy = max(0, available_energy - daily_energy)
        used_energy = max(0, max_energy - available_energy)
        sources.append({
            "kind": "tier",
            "source": "subscription" if (tier.price_cents > 0 and tier.is_recurring) else ("paid" if tier.price_cents > 0 else "free"),
            "tier_id": tier_id_str,
            "tier_name": tier.name,
            "daily_energy": daily_energy,
            "max_energy": max_energy,
            "available_energy": available_energy,
            "saved_energy": saved_energy,
            "used_energy": used_energy,
            "saved_days": saved_energy // daily_energy if daily_energy > 0 else 0,
            "is_throttled": is_throttled,
            "wait_seconds": int(wait_time.total_seconds()),
            "next_reset_at": _next_utc_midnight() if daily_energy > 0 else None,
        })

    if not sources:
        return UserImageEnergyResponse(status="none", sources=[])

    return UserImageEnergyResponse(
        status="active",
        total_daily_energy=sum(s["daily_energy"] for s in sources),
        total_max_energy=sum(s["max_energy"] for s in sources),
        total_available_energy=sum(s["available_energy"] for s in sources),
        total_saved_energy=sum(s["saved_energy"] for s in sources),
        total_used_energy=sum(s["used_energy"] for s in sources),
        next_reset_at=next((s["next_reset_at"] for s in sources if s.get("next_reset_at")), None),
        sources=sources,
    )
