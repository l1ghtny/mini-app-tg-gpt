from datetime import datetime
from sqlmodel import select, func
from sqlmodel.ext.asyncio.session import AsyncSession
import uuid

from app.db.subscription_tiers import SubscriptionTier, TierModelLimit, UserSubscription
from app.db.models import RequestLedger


async def month_start_expr():
    return func.date_trunc("month", func.now())


async def get_active_tier(session: AsyncSession, user_id: uuid.UUID) -> SubscriptionTier | None:
    q = (
        select(SubscriptionTier)
        .join(UserSubscription, UserSubscription.tier_id == SubscriptionTier.id)
        .where(UserSubscription.user_id==user_id, UserSubscription.status=="active",
               (UserSubscription.expires_at.is_(None)) | (UserSubscription.expires_at > func.now()))
        .limit(1)
    )
    return (await session.exec(q)).first()


async def remaining_requests_for_model(session, user_id, tier_id, model_name) -> int:
    # monthly cap for this model
    cap_row = (await session.exec(
        select(TierModelLimit.monthly_requests).where(
            TierModelLimit.tier_id==tier_id, TierModelLimit.model_name==model_name
        ).limit(1)
    )).first()
    cap = cap_row or 0
    if cap == 0:  # 0 means unlimited in this design
        return 10_000_000

    start = await month_start_expr()
    used = (await session.exec(
        select(func.count())
        .where(RequestLedger.user_id==user_id,
               RequestLedger.model_name==model_name,
               RequestLedger.feature=="text",
               RequestLedger.state.in_(("reserved","consumed")),
               RequestLedger.created_at >= start)
    )).one()
    return max(0, cap - (used or 0))


async def remaining_images(session, user_id, tier: SubscriptionTier) -> int:
    cap = tier.monthly_images or 0
    if cap == 0:  # unlimited
        return 10_000_000
    start = await month_start_expr()
    used = (await session.exec(
        select(func.count())
        .where(RequestLedger.user_id==user_id,
               RequestLedger.feature=="image",
               RequestLedger.state.in_(("reserved","consumed")),
               RequestLedger.created_at >= start)
    )).one()
    return max(0, cap - (used or 0))


# requests in real time


async def reserve_request(session, *, user_id, conversation_id, assistant_message_id,
                          request_id, model_name, feature, tool_choice=None):

    # try insert; on duplicate (same request_id), just return the existing row

    rl = RequestLedger(user_id=user_id, conversation_id=conversation_id,
                       assistant_message_id=assistant_message_id,
                       request_id=request_id, model_name=model_name,
                       feature=feature, tool_choice=tool_choice, state="reserved")
    session.add(rl)
    try:
        await session.commit()
        await session.refresh(rl)
        return rl
    except Exception:
        await session.rollback()
        # fetch existing
        rl = (await session.exec(
            select(RequestLedger).where(RequestLedger.user_id==user_id,
                                        RequestLedger.request_id==request_id)
        )).first()
        return rl


async def finalize_request(session, *, request_id, user_id, success: bool):
    rl = (await session.exec(
        select(RequestLedger).where(RequestLedger.user_id==user_id, RequestLedger.request_id==request_id)
    )).first()
    if rl:
        rl.state = "consumed" if success else "refunded"
        await session.commit()