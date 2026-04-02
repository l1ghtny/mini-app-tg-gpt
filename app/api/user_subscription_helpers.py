from calendar import monthrange
from datetime import datetime

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.orm import selectinload
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.metrics import track_event
from app.db.models import PaymentMethod
from app.db.subscription_tiers import SubscriptionStatus, UserSubscription
from app.schemas.subscriptions import (
    ActiveSubscriptionsResponse,
    CancelSubscriptionResponse,
    SubscriptionResponse,
)
from app.services.subscription_check.entitlements import get_current_subscription


def _add_one_calendar_month(dt: datetime) -> datetime:
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    day = min(dt.day, monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _subscription_priority_key(sub: UserSubscription) -> tuple[int, int, int, datetime]:
    tier = sub.tier
    if tier.price_cents > 0 and getattr(tier, "is_recurring", True):
        source_rank = 3
    elif tier.price_cents > 0:
        source_rank = 2
    else:
        source_rank = 1
    return (
        source_rank,
        tier.price_cents,
        tier.index or 0,
        sub.started_at or datetime.min,
    )


def _format_ts(dt: datetime | None) -> str | None:
    return dt.strftime("%H:%M:%S %d.%m.%Y") if dt else None


async def get_active_subscription(session: AsyncSession, user) -> ActiveSubscriptionsResponse:
    query = (
        select(UserSubscription)
        .where(
            UserSubscription.user_id == user.id,
            UserSubscription.status == SubscriptionStatus.active,
            (UserSubscription.expires_at.is_(None)) | (UserSubscription.expires_at > func.now()),
        )
        .options(selectinload(UserSubscription.tier))
    )
    subscriptions = (await session.exec(query)).all()
    if not subscriptions:
        raise HTTPException(status_code=403, detail="No active subscription found")

    ordered = sorted(subscriptions, key=_subscription_priority_key, reverse=True)
    active_subscriptions: list[SubscriptionResponse] = []
    for sub in ordered:
        expires_at = sub.expires_at
        if getattr(sub.tier, "is_recurring", True) and expires_at is None:
            expires_at = _add_one_calendar_month(sub.started_at)

        active_subscriptions.append(
            SubscriptionResponse(
                subscription_id=str(sub.id),
                status=sub.status,
                started_at=sub.started_at.strftime("%H:%M:%S %d.%m.%Y"),
                expires_at=_format_ts(expires_at),
                tier_name=sub.tier.name,
                tier_name_ru=sub.tier.name_ru,
                tier_description=sub.tier.description,
                tier_description_ru=sub.tier.description_ru,
                tier_price=sub.tier.price_cents,
                tier_id=str(sub.tier.id),
            )
        )

    return ActiveSubscriptionsResponse(
        active_subscriptions=active_subscriptions,
        primary_subscription_id=str(ordered[0].id),
    )


async def cancel_subscription(
    session: AsyncSession,
    user,
    background_tasks: BackgroundTasks,
) -> CancelSubscriptionResponse:
    sub = await get_current_subscription(session, user.id)
    if not sub:
        raise HTTPException(status_code=400, detail="No active subscription")

    is_recurring = getattr(sub.tier, "is_recurring", True)
    if not is_recurring:
        raise HTTPException(
            status_code=400,
            detail="This plan cannot be cancelled (it is non-renewing).",
        )

    if sub.tier.price_cents == 0:
        raise HTTPException(status_code=400, detail="Cannot cancel a free plan.")

    payment_methods = await session.exec(
        select(PaymentMethod).where(
            PaymentMethod.user_id == user.id,
            PaymentMethod.is_default == True,
        )
    )

    pms = payment_methods.all()
    if not pms:
        raise HTTPException(status_code=400, detail="No active payment method found to cancel.")

    for pm in pms:
        await session.delete(pm)

    background_tasks.add_task(
        track_event,
        "subscription_cancelled",
        str(user.id),
        {"tier": sub.tier.name},
    )

    await session.commit()
    return CancelSubscriptionResponse(status="success", message="Auto-renewal cancelled.")
