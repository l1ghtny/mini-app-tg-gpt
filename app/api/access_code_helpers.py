from datetime import datetime

from dateutil.relativedelta import relativedelta
from fastapi import HTTPException
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.subscription_tiers import (
    AccessCode,
    AccessCodeDiscount,
    SubscriptionTier,
    UsagePack,
    UsagePackSource,
    UserSubscription,
    UserTierDiscount,
    UserUsagePack,
)
from app.schemas.codes import (
    AccessCodeAdminResponse,
    AccessCodeCreate,
    AccessCodeDiscountOut,
    AccessCodeResponse,
    AccessCodeRedeemResponse,
)
from app.schemas.subscriptions import (
    SubscriptionTierResponse,
    TierMonthlyLimits,
    UsagePackImageModelLimitResponse,
    UsagePackModelLimitResponse,
    UsagePackResponse,
)


async def fetch_access_code_by_code(session: AsyncSession, code: str) -> AccessCode:
    result = await session.exec(
        select(AccessCode)
        .where(AccessCode.code == code)
        .options(
            selectinload(AccessCode.tier).selectinload(SubscriptionTier.tier_model_limits),
            selectinload(AccessCode.tier).selectinload(SubscriptionTier.tier_image_model_limits),
            selectinload(AccessCode.tier).selectinload(SubscriptionTier.tier_image_quality_limits),
            selectinload(AccessCode.discounts).selectinload(AccessCodeDiscount.tier),
            selectinload(AccessCode.usage_pack).selectinload(UsagePack.pack_model_limits),
            selectinload(AccessCode.usage_pack).selectinload(UsagePack.pack_image_model_limits),
        )
    )
    access_code = result.first()
    if not access_code:
        raise HTTPException(status_code=404, detail="Access code not found")
    return access_code


async def fetch_access_code_by_id(session: AsyncSession, code_id: str) -> AccessCode:
    result = await session.exec(
        select(AccessCode)
        .where(AccessCode.id == code_id)
        .options(selectinload(AccessCode.discounts))
    )
    access_code = result.first()
    if not access_code:
        raise HTTPException(status_code=404, detail="Access code not found")
    return access_code


def ensure_access_code_valid(access_code: AccessCode, now: datetime | None = None) -> None:
    now = now or datetime.now()
    if access_code.expires_at and access_code.expires_at < now:
        raise HTTPException(status_code=400, detail="Access code has expired")
    if access_code.max_uses is not None and access_code.used_count >= access_code.max_uses:
        raise HTTPException(status_code=400, detail="Access code usage limit reached")


def _build_tier_response(tier: SubscriptionTier) -> SubscriptionTierResponse:
    allowed_models = sorted({l.image_model for l in tier.tier_image_model_limits})
    allowed_qualities = sorted({l.quality for l in tier.tier_image_quality_limits})
    return SubscriptionTierResponse(
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
        is_recurring=tier.is_recurring,
        daily_image_limit=tier.daily_image_limit,
        allowed_image_qualities=allowed_qualities,
        allowed_image_models=allowed_models,
        tier_id=str(tier.id),
    )


def _build_pack_response(pack: UsagePack) -> UsagePackResponse:
    return UsagePackResponse(
        id=str(pack.id),
        name=pack.name,
        name_ru=pack.name_ru,
        description=pack.description,
        description_ru=pack.description_ru,
        price_cents=pack.price_cents,
        is_active=pack.is_active,
        is_public=pack.is_public,
        index=pack.index,
        model_limits=[
            UsagePackModelLimitResponse(
                model_name=l.model_name,
                request_credits=l.request_credits,
            )
            for l in pack.pack_model_limits
        ],
        image_model_limits=[
            UsagePackImageModelLimitResponse(
                image_model=l.image_model,
                credit_amount=l.credit_amount,
            )
            for l in pack.pack_image_model_limits
        ],
    )


def _build_discounts(access_code: AccessCode) -> list[AccessCodeDiscountOut]:
    discounts_out: list[AccessCodeDiscountOut] = []
    for discount in access_code.discounts:
        tier_name = discount.tier.name if discount.tier else "Unknown tier"
        discounts_out.append(
            AccessCodeDiscountOut(
                id=discount.id,
                tier_id=discount.tier_id,
                tier_name=tier_name,
                discount_percent=discount.discount_percent,
                duration_months=discount.duration_months,
            )
        )
    return discounts_out


def build_access_code_response(access_code: AccessCode) -> AccessCodeResponse:
    tier_out = _build_tier_response(access_code.tier) if access_code.tier else None
    pack_out = _build_pack_response(access_code.usage_pack) if access_code.usage_pack else None
    discounts_out = _build_discounts(access_code)

    return AccessCodeResponse(
        id=access_code.id,
        code=access_code.code,
        tier=tier_out,
        usage_pack=pack_out,
        discounts=discounts_out,
        max_uses=access_code.max_uses,
        expires_at=datetime.now() + relativedelta(days=access_code.tier_expires_in_days),
    )


async def redeem_access_code_for_user(
    session: AsyncSession,
    user,
    access_code: AccessCode,
) -> AccessCodeRedeemResponse:
    now = datetime.now()

    if access_code.tier_id:
        existing_sub_result = await session.exec(
            select(UserSubscription).where(
                UserSubscription.user_id == user.id,
                UserSubscription.tier_id == access_code.tier_id,
                UserSubscription.status == "active",
            )
        )
        existing_sub = existing_sub_result.first()

        expiration = access_code.tier_expires_in_days
        if expiration > 0:
            expires_at = now + relativedelta(days=expiration)
        else:
            expires_at = now + relativedelta(years=10)

        if not existing_sub:
            subscription_for_user = UserSubscription(
                user_id=user.id,
                tier_id=access_code.tier_id,
                status="active",
                expires_at=expires_at,
            )
            session.add(subscription_for_user)

    if access_code.usage_pack_id:
        pack = await session.get(UsagePack, access_code.usage_pack_id)
        if pack and pack.is_active:
            expires_at = None
            expiration = access_code.tier_expires_in_days
            if expiration > 0:
                expires_at = now + relativedelta(days=expiration)

            pack_purchase = UserUsagePack(
                user_id=user.id,
                pack_id=pack.id,
                source=UsagePackSource.free,
                purchased_at=now,
                expires_at=expires_at,
                note=f"Access code {access_code.code}",
            )
            session.add(pack_purchase)

    for discount in access_code.discounts:
        months = discount.duration_months or 0
        if months > 0:
            valid_until = now + relativedelta(months=months)
        else:
            valid_until = now + relativedelta(years=10)

        user_discount = UserTierDiscount(
            user_id=user.id,
            tier_id=discount.tier_id,
            discount_percent=discount.discount_percent,
            valid_until=valid_until,
            access_code_id=access_code.id,
        )
        session.add(user_discount)

    access_code.used_count += 1
    await session.commit()
    return AccessCodeRedeemResponse(status="ok")


async def create_access_code(
    session: AsyncSession,
    payload: AccessCodeCreate,
) -> AccessCodeAdminResponse:
    code = AccessCode(
        code=payload.code,
        max_uses=payload.max_uses or 1,
        expires_at=payload.expires_at,
        tier_id=payload.grant_tier_id,
        usage_pack_id=payload.grant_usage_pack_id,
    )

    session.add(code)
    await session.flush()

    for discount in payload.discounts:
        session.add(
            AccessCodeDiscount(
                access_code_id=code.id,
                tier_id=discount.tier_id,
                discount_percent=discount.percent,
                duration_months=discount.duration_months or 1,
            )
        )

    await session.commit()
    await session.refresh(code)
    return AccessCodeAdminResponse(
        id=code.id,
        code=code.code,
        tier_id=code.tier_id,
        usage_pack_id=code.usage_pack_id,
        tier_expires_in_days=code.tier_expires_in_days,
        max_uses=code.max_uses,
        used_count=code.used_count,
        expires_at=code.expires_at,
    )
