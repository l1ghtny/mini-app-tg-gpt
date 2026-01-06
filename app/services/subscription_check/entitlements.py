import uuid
from datetime import datetime

from sqlalchemy.orm import selectinload
from sqlmodel import select, func
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import RequestLedger
from app.db.subscription_tiers import SubscriptionTier, TierModelLimit, UserSubscription, SubscriptionStatus


async def month_start_expr():
    return func.date_trunc("month", func.now())


async def get_active_tier(session: AsyncSession, user_id: uuid.UUID) -> SubscriptionTier | None:
    q = (
        select(SubscriptionTier)
        .join(UserSubscription, UserSubscription.tier_id == SubscriptionTier.id)
        .where(UserSubscription.user_id==user_id, UserSubscription.status=="active",
               (UserSubscription.expires_at.is_(None)) | (UserSubscription.expires_at > func.now()))
        .limit(1).options(selectinload(SubscriptionTier.tier_model_limits))
    )
    return (await session.exec(q)).first()





async def get_usage_start_date(session: AsyncSession, user_id: uuid.UUID, tier: SubscriptionTier) -> datetime:
    """
    Returns the start timestamp for counting usage.
    - Recurring Tiers: Start of the current month.
    - One-Time/Starter Tiers: Start of the subscription itself.
    """
    # Assuming 'is_recurring' field exists on SubscriptionTier.
    # If not present yet, default to True (monthly behavior).
    is_recurring = getattr(tier, "is_recurring", True)

    if is_recurring:
        # PostgreSQL specific: Date truncation for start of month
        # Note: If you use SQLite for testing, this func needs a fallback
        return func.date_trunc("month", func.now())
    else:
        # Fetch the active subscription to get its start date
        sub = (await session.exec(
            select(UserSubscription)
            .where(
                UserSubscription.user_id == user_id,
                UserSubscription.tier_id == tier.id,
                UserSubscription.status == SubscriptionStatus.active
            )
        )).first()
        if sub:
            return sub.started_at

        # Fallback (shouldn't happen if user has tier): Start of month
        return func.date_trunc("month", func.now())


async def remaining_requests_for_model(session: AsyncSession, user_id: uuid.UUID, tier_id: uuid.UUID,
                                       model_name: str) -> int:
    tier = await session.get(SubscriptionTier, tier_id)
    if not tier:
        return 0

    # 1. Get Cap from Database
    cap_row = (await session.exec(
        select(TierModelLimit.monthly_requests).where(
            TierModelLimit.tier_id == tier_id,
            TierModelLimit.model_name == model_name
        ).limit(1)
    )).first()

    # LOGIC FIX: If no limit defined, return 0 (Access Denied), not 10M.
    # If you want specific models to be "Unlimited", set a high number in DB (e.g. 1000000)
    cap = cap_row or 0
    if cap == 0:
        return 0

    # 2. Determine Time Window
    start_expr = await get_usage_start_date(session, user_id, tier)

    # 3. Count Usage
    used = (await session.exec(
        select(func.count())
        .where(
            RequestLedger.user_id == user_id,
            RequestLedger.model_name == model_name,
            RequestLedger.feature == "text",
            RequestLedger.state.in_(("reserved", "consumed")),
            RequestLedger.created_at >= start_expr
        )
    )).one()

    return max(0, cap - (used or 0))


async def remaining_images(session: AsyncSession, user_id: uuid.UUID, tier: SubscriptionTier) -> int:
    cap = tier.monthly_images or 0
    if cap == 0:
        return 0

    start_expr = await get_usage_start_date(session, user_id, tier)

    # NEW: Weighted Calculation
    # 1.5 costs 2, others cost 1
    statement = select(
        RequestLedger.model_name,
        func.count(RequestLedger.id)
    ).where(
        RequestLedger.user_id == user_id,
        RequestLedger.feature == "image",
        RequestLedger.state.in_(("reserved", "consumed")),
        RequestLedger.created_at >= start_expr
    ).group_by(RequestLedger.model_name)

    results = (await session.exec(statement)).all()

    used_total = 0
    for model_name, count in results:
        if model_name == "gpt-image-1.5":
            used_total += (count * 2)
        else:
            used_total += count

    return max(0, cap - used_total)


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