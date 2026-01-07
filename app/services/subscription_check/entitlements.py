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





async def usage_window_start_expr(session: AsyncSession, user_id: uuid.UUID, tier: SubscriptionTier):
    """
    Returns the start timestamp for counting usage for a given tier/subscription.

    - Non-recurring tiers: count everything since subscription started_at.
    - Recurring tiers: count since the latest "billing cycle boundary" based on the
      subscription start day-of-month (e.g., started on the 4th => boundaries are every month on the 4th).
      If today is before that day-of-month, we go to the previous month boundary.

    Notes:
    - This uses PostgreSQL date functions (make_date, extract, date_trunc). If you run tests on SQLite,
      you may need a fallback implementation.
    """
    is_recurring = getattr(tier, "is_recurring", True)

    sub = (await session.exec(
        select(UserSubscription)
        .where(
            UserSubscription.user_id == user_id,
            UserSubscription.tier_id == tier.id,
            UserSubscription.status == SubscriptionStatus.active
        )
        .order_by(UserSubscription.started_at.desc())
        .limit(1)
    )).first()

    # Fallback: if subscription row can't be found, use calendar month start (best-effort)
    if not sub or not sub.started_at:
        return func.date_trunc("month", func.now())

    if not is_recurring:
        return sub.started_at

    # Recurring:
    # Compute boundary for "this month at day=sub.started_at.day", clamped to month length.
    # If now < boundary => use previous month's boundary.
    now_ts = func.now()
    start_day = func.extract("day", sub.started_at)

    month_start = func.date_trunc("month", now_ts)
    this_month_last_day = func.extract("day", (month_start + func.interval("1 month") - func.interval("1 day")))

    this_boundary_date = func.make_date(
        func.extract("year", now_ts),
        func.extract("month", now_ts),
        func.least(start_day, this_month_last_day)
    )

    prev_month_ts = now_ts - func.interval("1 month")
    prev_month_start = func.date_trunc("month", prev_month_ts)
    prev_month_last_day = func.extract("day", (prev_month_start + func.interval("1 month") - func.interval("1 day")))

    prev_boundary_date = func.make_date(
        func.extract("year", prev_month_ts),
        func.extract("month", prev_month_ts),
        func.least(start_day, prev_month_last_day)
    )

    # If current time is before this month's boundary, cycle started at previous boundary.
    return func.case(
        (now_ts < this_boundary_date, prev_boundary_date),
        else_=this_boundary_date,
    )


async def get_usage_start_date(session: AsyncSession, user_id: uuid.UUID, tier: SubscriptionTier) -> datetime:
    """
    Deprecated wrapper: kept for compatibility.
    Prefer usage_window_start_expr().
    """
    return await usage_window_start_expr(session, user_id, tier)


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