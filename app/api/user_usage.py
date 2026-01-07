from fastapi import Depends, APIRouter
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, func
import uuid

from app.api.dependencies import get_current_user
from app.db.database import get_session
from app.db.subscription_tiers import SubscriptionTier, TierModelLimit, UserSubscription
from app.db.models import RequestLedger
from app.services.subscription_check.entitlements import remaining_images

user_usage = APIRouter(tags=['user/usage'], prefix="/user/usage")




async def _active_tier(session: AsyncSession, user_id: uuid.UUID):
    q = (
        select(SubscriptionTier)
        .join(UserSubscription, UserSubscription.tier_id == SubscriptionTier.id)
        .where(UserSubscription.user_id==user_id, UserSubscription.status=="active",
               (UserSubscription.expires_at.is_(None)) | (UserSubscription.expires_at > func.now()))
        .limit(1)
    )
    return (await session.exec(q)).first()

@user_usage.get("/me/models")
async def my_model_usage(session: AsyncSession = Depends(get_session), user=Depends(get_current_user)):
    tier = await _active_tier(session, user.id)
    if not tier:
        return {"status": "none", "models": []}

    start = func.date_trunc("month", func.now())

    # caps per model
    caps = (await session.exec(
        select(TierModelLimit.model_name, TierModelLimit.monthly_requests)
        .where(TierModelLimit.tier_id == tier.id)
    )).all()

    # used per model
    used_rows = (await session.exec(
        select(RequestLedger.model_name, func.count())
        .where(RequestLedger.user_id==user.id,
               RequestLedger.feature=="text",
               RequestLedger.state.in_(("reserved","consumed")),
               RequestLedger.created_at >= start)
        .group_by(RequestLedger.model_name)
    )).all()
    used = {m: c for m, c in used_rows}

    models = []
    for model_name, cap in caps:
        cap = cap or 0
        rem = (10_000_000 if cap == 0 else max(0, cap - used.get(model_name, 0)))
        models.append({
            "model": model_name,
            "cap": cap,            # 0 => unlimited
            "used": used.get(model_name, 0),
            "remaining": rem
        })

    return {"status":"active", "models": models}

@user_usage.get("/me/features")
async def my_feature_usage(session: AsyncSession = Depends(get_session), user=Depends(get_current_user)):
    tier = await _active_tier(session, user.id)
    if not tier:
        return {"status": "none", "features": {}}

    start = func.date_trunc("month", func.now())

    # images
    img_cap = tier.monthly_images or 0
    img_used = (await session.exec(
        select(func.count()).where(
            RequestLedger.user_id==user.id,
            RequestLedger.feature=="image",
            RequestLedger.state.in_(("reserved","consumed")),
            RequestLedger.created_at >= start
        )
    )).one() or 0

    # Use the unified entitlement logic for remaining images so that
    # models like "gpt-image-1.5" are weighted as 2, matching backend checks.
    img_remaining = await remaining_images(session, user.id, tier)

    return {
        "status": "active",
        "features": {
            "images": {"cap": img_cap, "used": img_used, "remaining": img_remaining},
            # add docs, deepsearch similarly when you turn them on
        }
    }