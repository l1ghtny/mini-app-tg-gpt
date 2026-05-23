from calendar import monthrange
from datetime import datetime
import re

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.orm import selectinload
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.metrics import track_event
from app.db.models import Payment, PaymentMethod, PaymentProductType
from app.db.subscription_tiers import SubscriptionStatus, SubscriptionTier, UserSubscription, UserTierDiscount
from app.schemas.subscriptions import (
    ActiveSubscriptionsResponse,
    CancelSubscriptionResponse,
    SubscriptionDiscountResponse,
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
    return dt.isoformat(timespec="seconds") if dt else None


def _tier_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "tier"


async def get_active_subscription(session: AsyncSession, user) -> ActiveSubscriptionsResponse:
    default_payment_method = await session.exec(
        select(PaymentMethod.id).where(
            PaymentMethod.user_id == user.id,
            PaymentMethod.is_default == True,
        )
    )
    has_default_payment_method = default_payment_method.first() is not None

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

    discounts = await _load_active_discounts(session, user.id)
    first_purchase_available = await _first_purchase_available(session, user.id)

    ordered = sorted(subscriptions, key=_subscription_priority_key, reverse=True)
    active_subscriptions: list[SubscriptionResponse] = []
    for sub in ordered:
        expires_at = sub.expires_at
        is_recurring_tier = bool(getattr(sub.tier, "is_recurring", True))
        is_paid_tier = sub.tier.price_cents > 0
        auto_renew = is_recurring_tier and is_paid_tier and has_default_payment_method

        if is_recurring_tier and expires_at is None:
            expires_at = _add_one_calendar_month(sub.started_at)

        active_subscriptions.append(
            SubscriptionResponse(
                subscription_id=str(sub.id),
                status=sub.status,
                started_at=_format_ts(sub.started_at),
                expires_at=_format_ts(expires_at),
                is_recurring=is_recurring_tier,
                auto_renew=auto_renew,
                can_cancel=auto_renew,
                cancel_at_period_end=is_recurring_tier and is_paid_tier and not auto_renew,
                tier_name=sub.tier.name,
                tier_slug=_tier_slug(sub.tier.name),
                tier_rank=sub.tier.index or 0,
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
        discounts=discounts,
        first_purchase_available=first_purchase_available,
    )


async def _load_active_discounts(
    session: AsyncSession,
    user_id,
) -> list[SubscriptionDiscountResponse]:
    query = (
        select(UserTierDiscount, SubscriptionTier)
        .join(SubscriptionTier, UserTierDiscount.tier_id == SubscriptionTier.id)
        .where(
            UserTierDiscount.user_id == user_id,
            UserTierDiscount.valid_until > func.now(),
        )
        .options(selectinload(UserTierDiscount.access_code))
    )
    rows = (await session.exec(query)).all()

    discounts: list[SubscriptionDiscountResponse] = []
    for discount, tier in rows:
        code = discount.access_code.code if getattr(discount, "access_code", None) else None
        discounts.append(
            SubscriptionDiscountResponse(
                code=code,
                percent_off=int(discount.discount_percent or 0),
                applies_to=[_tier_slug(tier.name)],
                expires_at=_format_ts(discount.valid_until),
                stackable=True,
            )
        )
    return discounts


async def _first_purchase_available(session: AsyncSession, user_id) -> bool:
    existing_paid_subscription = await session.exec(
        select(Payment.id).where(
            Payment.user_id == user_id,
            Payment.product_type == PaymentProductType.subscription,
            Payment.amount > 0,
            Payment.tbank_status == "CONFIRMED",
        )
    )
    return existing_paid_subscription.first() is None


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
