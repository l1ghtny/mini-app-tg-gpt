from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.dependencies import get_current_user
from app.db.database import get_session
from app.db.subscription_tiers import AccessCode, SubscriptionTier, UserSubscription, AccessCodeDiscount
from app.schemas.codes import AccessCodeCreate
from app.schemas.subscriptions import AccessCodeResponse, TierMonthlyLimits

access_codes = APIRouter(tags=['access codes'], prefix='/access_codes')

@access_codes.get("/{code}", response_model=AccessCodeResponse, status_code=200, responses={200: {"description": "Access code found"}, 404: {"description": "Access code not found"}})
async def get_access_code_info(code: str, session: AsyncSession = Depends(get_session), user=Depends(get_current_user)):
    access_code = (await session.exec(select(AccessCode).where(AccessCode.code == code).options(selectinload(AccessCode.tier).selectinload(SubscriptionTier.tier_model_limits)))).first()
    # print(access_code.id)
    if not access_code:
        raise HTTPException(status_code=404, detail="Access code not found")
    return AccessCodeResponse(
        id=str(access_code.id),
        code=access_code.code,
        tier_name=access_code.tier.name,
        tier_price=access_code.tier.price_cents,
        tier_monthly_images=access_code.tier.monthly_images,
        tier_monthly_limits=[TierMonthlyLimits(model_name=l.model_name, requests_limit=l.monthly_requests) for l in access_code.tier.tier_model_limits],
        discount_percent=access_code.discount_percent,
        discount_months=access_code.discount_months
    )


@access_codes.post("/{code_id}/redeem")
async def redeem_access_code(code_id: str, session: AsyncSession = Depends(get_session), user=Depends(get_current_user)):
    # TODO: add a check for the code expiration date and if user already has an active subscription
    access_code = (await session.exec(select(AccessCode).where(AccessCode.id == code_id))).first()
    if not access_code:
        return HTTPException(status_code=404, detail="Access code not found")
    elif access_code.used_count >= access_code.max_uses:
        return HTTPException(status_code=403, detail="Access code has been used too many times")
    else:
        access_code.used_count += 1
        subscription_for_user = UserSubscription(user_id=user.id, tier_id=access_code.tier_id, status="active", started_at=datetime.now(), expires_at=(datetime.now() + timedelta(days=30)), discount_percent=access_code.discount_percent, discount_expires_at=(datetime.now() + relativedelta(months=access_code.discount_months)))
        session.add(subscription_for_user)
        await session.commit()
        return status.HTTP_202_ACCEPTED


@access_codes.post("/api/v1/admin/access-codes")
async def create_access_code(payload: AccessCodeCreate, session: AsyncSession = Depends(get_session), user=Depends(get_current_user)):
    code = AccessCode(
        code=payload.code,
        max_uses=payload.max_uses,
        expires_at=payload.expires_at,
        grant_tier_id=payload.grant_tier_id,
    )
    session.add(code)
    await session.flush()  # to get code.id

    for d in payload.discounts:
        session.add(
            AccessCodeDiscount(
                access_code_id=code.id,
                tier_id=d.tier_id,
                percent=d.percent,
                duration_months=d.duration_months,
            )
        )

    await session.commit()
    await session.refresh(code)
    return code
