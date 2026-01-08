import uuid
from datetime import datetime

from sqlalchemy import DateTime, text
from sqlalchemy.orm import selectinload
from sqlmodel import select, func, case, cast
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





def _days_in_month(year: int, month: int) -> int:
    # month: 1..12
    if month == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month + 1, 1)
    this_month = datetime(year, month, 1)
    return (next_month - this_month).days


def _add_months(year: int, month: int, delta_months: int) -> tuple[int, int]:
    # returns (year, month) with month 1..12
    total = (year * 12 + (month - 1)) + delta_months
    new_year = total // 12
    new_month = (total % 12) + 1
    return new_year, new_month


def _latest_billing_boundary(now: datetime, anchor_day: int) -> datetime:
    """
    Given current time `now` and anchor day-of-month (1..31),
    returns the latest boundary datetime (00:00) not in the future.

    If anchor_day doesn't exist in a month, clamps to last day of that month.
    """
    y, m = now.year, now.month
    dim = _days_in_month(y, m)
    this_day = min(anchor_day, dim)
    this_boundary = datetime(y, m, this_day, 0, 0, 0)

    if now >= this_boundary:
        return this_boundary

    py, pm = _add_months(y, m, -1)
    pdim = _days_in_month(py, pm)
    prev_day = min(anchor_day, pdim)
    return datetime(py, pm, prev_day, 0, 0, 0)


async def usage_window_start_dt(session: AsyncSession, user_id: uuid.UUID, tier: SubscriptionTier) -> datetime:
    """
    Python-based usage window start:
    - Non-recurring tiers: since subscription started_at
    - Recurring tiers: since last billing boundary based on started_at day-of-month
    """
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

    # Safe fallback (shouldn't happen): calendar month start in Python
    now = datetime.utcnow()
    if not sub or not sub.started_at:
        return datetime(now.year, now.month, 1, 0, 0, 0)

    if not getattr(tier, "is_recurring", True):
        return sub.started_at

    anchor_day = sub.started_at.day
    return _latest_billing_boundary(now=now, anchor_day=anchor_day)


async def get_usage_start_date(session: AsyncSession, user_id: uuid.UUID, tier: SubscriptionTier) -> datetime:
    """
    Deprecated wrapper: kept for compatibility.
    Prefer usage_window_start_dt().
    """
    return await usage_window_start_dt(session, user_id, tier)


async def remaining_requests_for_model(session: AsyncSession, user_id: uuid.UUID, tier_id: uuid.UUID,
                                       model_name: str) -> int:
    tier = await session.get(SubscriptionTier, tier_id)
    if not tier:
        return 0

    cap_row = (await session.exec(
        select(TierModelLimit.monthly_requests).where(
            TierModelLimit.tier_id == tier_id,
            TierModelLimit.model_name == model_name
        ).limit(1)
    )).first()

    cap = cap_row or 0
    if cap == 0:
        return 0

    # Python window start (no SQL CASE/make_date/interval)
    start_dt = await usage_window_start_dt(session, user_id, tier)

    used = (await session.exec(
        select(func.count())
        .where(
            RequestLedger.user_id == user_id,
            RequestLedger.model_name == model_name,
            RequestLedger.feature == "text",
            RequestLedger.state.in_(("reserved", "consumed")),
            RequestLedger.created_at >= start_dt
        )
    )).one()

    return max(0, cap - (used or 0))


async def remaining_images(session: AsyncSession, user_id: uuid.UUID, tier: SubscriptionTier) -> int:
    cap = tier.monthly_images or 0
    if cap == 0:
        return 0

    start_dt = await usage_window_start_dt(session, user_id, tier)

    statement = select(
        RequestLedger.model_name,
        func.count(RequestLedger.id)
    ).where(
        RequestLedger.user_id == user_id,
        RequestLedger.feature == "image",
        RequestLedger.state.in_(("reserved", "consumed")),
        RequestLedger.created_at >= start_dt
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