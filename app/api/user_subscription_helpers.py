from fastapi import BackgroundTasks, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.metrics import track_event
from app.db.models import PaymentMethod
from app.schemas.subscriptions import CancelSubscriptionResponse, SubscriptionResponse
from app.services.subscription_check.entitlements import get_current_subscription


async def get_active_subscription(session: AsyncSession, user) -> SubscriptionResponse:
    user_subscription = await get_current_subscription(session, user.id)
    if not user_subscription:
        raise HTTPException(status_code=403, detail="No active subscription found")

    result = SubscriptionResponse(
        subscription_id=str(user_subscription.id),
        status=user_subscription.status,
        started_at=user_subscription.started_at.strftime("%H:%M:%S %d.%m.%Y"),
        expires_at=None,
        tier_name=user_subscription.tier.name,
        tier_name_ru=user_subscription.tier.name_ru,
        tier_description=user_subscription.tier.description,
        tier_description_ru=user_subscription.tier.description_ru,
        tier_price=user_subscription.tier.price_cents,
        tier_id=str(user_subscription.tier.id),
    )

    if user_subscription.tier.is_recurring == True:
        result.expires_at = user_subscription.expires_at.strftime("%H:%M:%S %d.%m.%Y")
    return result


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
