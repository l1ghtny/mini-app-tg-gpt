from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.dependencies import get_current_user
from app.core.metrics import track_event
from app.db.database import get_session
from app.db.models import PaymentMethod
from app.schemas.subscriptions import SubscriptionResponse
from app.services.subscription_check.entitlements import get_current_subscription

user_subscription = APIRouter(tags=['user/subscription'], prefix="/user/subscription")

@user_subscription.get("/active", response_model=SubscriptionResponse)
async def get_active_subscription(session: AsyncSession = Depends(get_session), user=Depends(get_current_user)):
    user_subscription = await get_current_subscription(session, user.id)
    if not user_subscription:
        raise HTTPException(status_code=403, detail="No active subscription found")
    else:
        result = SubscriptionResponse(
            subscription_id=str(user_subscription.id),
            status=user_subscription.status,
            started_at=user_subscription.started_at.strftime('%H:%M:%S %d.%m.%Y'),
            expires_at=None,
            tier_name=user_subscription.tier.name,
            tier_name_ru=user_subscription.tier.name_ru,
            tier_description=user_subscription.tier.description,
            tier_description_ru=user_subscription.tier.description_ru,
            tier_price=user_subscription.tier.price_cents,
            tier_id=str(user_subscription.tier.id)
        )

        if user_subscription.tier.is_recurring == True:
            result.expires_at = user_subscription.expires_at.strftime('%H:%M:%S %d.%m.%Y')
        return result


@user_subscription.post("/cancel")
async def cancel_subscription(
        background_tasks: BackgroundTasks,
        session: AsyncSession = Depends(get_session),
        user=Depends(get_current_user)
):
    """
    Cancels auto-renewal for PAiD, RECURRING subscriptions.
    """
    # Fetch active sub
    sub = await get_current_subscription(session, user.id)

    if not sub:
        raise HTTPException(status_code=400, detail="No active subscription")

    # Check if recurring
    is_recurring = getattr(sub.tier, "is_recurring", True)
    if not is_recurring:
        raise HTTPException(status_code=400, detail="This plan cannot be cancelled (it is non-renewing).")

    if sub.tier.price_cents == 0:
        raise HTTPException(status_code=400, detail="Cannot cancel a free plan.")

    # Remove Payment Method
    payment_methods = await session.exec(
        select(PaymentMethod).where(
            PaymentMethod.user_id == user.id,
            PaymentMethod.is_default == True
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
        {"tier": sub.tier.name}
    )

    await session.commit()

    return {"status": "success", "message": "Auto-renewal cancelled."}
