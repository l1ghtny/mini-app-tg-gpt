import logging
from datetime import datetime, timedelta
from typing import List

from dateutil.relativedelta import relativedelta
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.dependencies import get_current_user
from app.db.database import get_session
from app.db.subscription_tiers import (
    AccessCode,
    SubscriptionTier,
    UserSubscription,
    AccessCodeDiscount,
    UserTierDiscount,
    UsagePack,
    UserUsagePack,
    UsagePackSource,
)
from app.schemas.codes import (
    AccessCodeCreate,
    AccessCodeDiscountOut,
    AccessCodeResponse,
    AccessCodeRedeemResponse,
    AccessCodeAdminResponse,
)
from app.schemas.subscriptions import (
    TierMonthlyLimits,
    SubscriptionResponse,
    SubscriptionTierResponse,
    UsagePackResponse,
    UsagePackModelLimitResponse,
    UsagePackImageModelLimitResponse,
)

access_codes = APIRouter(tags=['access codes'], prefix='/access_codes')

@access_codes.get("/{code}", response_model=AccessCodeResponse)
async def get_access_code(
    code: str,
    session: AsyncSession = Depends(get_session),
):
    # Load code by its string code value, including tier + discounts + discount tiers

    result = await session.exec(
        select(AccessCode)
        .where(AccessCode.code == code)
        .options(
            selectinload(AccessCode.tier)
            .selectinload(SubscriptionTier.tier_model_limits),  # if you have this relationship
            selectinload(AccessCode.discounts)
            .selectinload(AccessCodeDiscount.tier),
            selectinload(AccessCode.usage_pack)
            .selectinload(UsagePack.pack_model_limits),
            selectinload(AccessCode.usage_pack)
            .selectinload(UsagePack.pack_image_model_limits),
        )
    )
    access_code = result.first()

    if not access_code:
        raise HTTPException(status_code=404, detail="Access code not found")

    now = datetime.now()

    # 2) Expiry and usage checks
    if access_code.expires_at and access_code.expires_at < now:
        raise HTTPException(status_code=400, detail="Access code has expired")

    if access_code.max_uses is not None and access_code.used_count >= access_code.max_uses:
        raise HTTPException(status_code=400, detail="Access code usage limit reached")

    # Build tier output (or None)
    tier_out = None
    if access_code.tier:
        # Assuming SubscriptionTierOut is compatible with model_validate / from_orm
        tier_out = SubscriptionTierResponse(name=access_code.tier.name, name_ru=access_code.tier.name_ru, description=access_code.tier.description, description_ru=access_code.tier.description_ru, price_cents=access_code.tier.price_cents, monthly_images=access_code.tier.monthly_images, tier_model_limits=[TierMonthlyLimits(model_name=l.model_name, requests_limit=l.monthly_requests) for l in access_code.tier.tier_model_limits], is_recurring=access_code.tier.is_recurring, daily_image_limit=access_code.tier.daily_image_limit, tier_id=str(access_code.tier.id))

    pack_out = None
    if access_code.usage_pack:
        pack_out = UsagePackResponse(
            id=str(access_code.usage_pack.id),
            name=access_code.usage_pack.name,
            name_ru=access_code.usage_pack.name_ru,
            description=access_code.usage_pack.description,
            description_ru=access_code.usage_pack.description_ru,
            price_cents=access_code.usage_pack.price_cents,
            is_active=access_code.usage_pack.is_active,
            is_public=access_code.usage_pack.is_public,
            index=access_code.usage_pack.index,
            model_limits=[
                UsagePackModelLimitResponse(
                    model_name=l.model_name,
                    request_credits=l.request_credits,
                )
                for l in access_code.usage_pack.pack_model_limits
            ],
            image_model_limits=[
                UsagePackImageModelLimitResponse(
                    image_model=l.image_model,
                    credit_amount=l.credit_amount,
                )
                for l in access_code.usage_pack.pack_image_model_limits
            ],
        )

    # Build discounts list
    discounts_out: list[AccessCodeDiscountOut] = []
    for d in access_code.discounts:
        tier_name = d.tier.name if d.tier else "Unknown tier"

        discounts_out.append(
            AccessCodeDiscountOut(
                id=d.id,
                tier_id=d.tier_id,
                tier_name=tier_name,
                discount_percent=d.discount_percent,
                duration_months=d.duration_months,
            )
        )

    return AccessCodeResponse(
        id=access_code.id,
        code=access_code.code,
        tier=tier_out,
        usage_pack=pack_out,
        discounts=discounts_out,
        max_uses=access_code.max_uses,
        expires_at=datetime.now() + relativedelta(days=access_code.tier_expires_in_days),
    )


@access_codes.post("/{code_id}/redeem", status_code=202, response_model=AccessCodeRedeemResponse)
async def redeem_access_code(
    code_id: str,
    session: AsyncSession = Depends(get_session),
    user=Depends(get_current_user),
):
    # 1) Load access code by its string value, including discounts
    result = await session.exec(
        select(AccessCode)
        .where(AccessCode.id == code_id)
        .options(selectinload(AccessCode.discounts))
    )
    access_code = result.first()

    if not access_code:
        raise HTTPException(status_code=404, detail="Access code not found")

    now = datetime.now()

    # 2) Expiry and usage checks
    if access_code.expires_at and access_code.expires_at < now:
        raise HTTPException(status_code=400, detail="Access code has expired")

    if access_code.max_uses is not None and access_code.used_count >= access_code.max_uses:
        raise HTTPException(status_code=400, detail="Access code usage limit reached")

    # 3) Optionally grant the tier right away (access_code.tier_id)
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
                expires_at=expires_at
                # any other fields you have on UserSubscription will just use defaults
            )
            session.add(subscription_for_user)

    # 3.1) Optionally grant a usage pack
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

    # 4) Create per-tier discounts for this user
    for discount in access_code.discounts:
        months = discount.duration_months or 0

        if months > 0:
            valid_until = now + relativedelta(months=months)
        else:
            # No duration given -> treat as a long-lived discount (10 years is effectively "forever" for our purposes)
            valid_until = now + relativedelta(years=10)

        user_discount = UserTierDiscount(
            user_id=user.id,
            tier_id=discount.tier_id,
            discount_percent=discount.discount_percent,
            valid_until=valid_until,
            access_code_id=access_code.id,
        )
        session.add(user_discount)

    # 5) Mark code as used
    access_code.used_count += 1

    await session.commit()

    # Keeping response dumb for now; front-end can be upgraded later
    return AccessCodeRedeemResponse(status="ok")


@access_codes.post("/admin/create", response_model=AccessCodeAdminResponse)
async def create_access_code(payload: AccessCodeCreate, session: AsyncSession = Depends(get_session), user=Depends(get_current_user)):
    code = AccessCode(
        code=payload.code,
        max_uses=payload.max_uses or 1,
        expires_at=payload.expires_at,
        tier_id=payload.grant_tier_id,
        usage_pack_id=payload.grant_usage_pack_id,
    )

    session.add(code)
    await session.flush()  # to get code.id

    for d in payload.discounts:
        session.add(
            AccessCodeDiscount(
                access_code_id=code.id,
                tier_id=d.tier_id,
                discount_percent=d.percent,
                duration_months=d.duration_months or 1,
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
