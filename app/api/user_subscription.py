from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.dependencies import get_current_user
from app.db import models, subscription_tiers
from app.db.database import get_session
from app.db.models import PaymentMethod
from app.schemas.subscriptions import SubscriptionResponse

user_subscription = APIRouter(tags=['user/subscription'], prefix="/user/subscription")

@user_subscription.get("/active", response_model=SubscriptionResponse)
async def get_active_subscription(session: AsyncSession = Depends(get_session), user=Depends(get_current_user)):
    get_subscription = select(subscription_tiers.UserSubscription).where(user.id==subscription_tiers.UserSubscription.user_id, subscription_tiers.UserSubscription.status=="active").options(selectinload(subscription_tiers.UserSubscription.tier))
    user_subscription = (await session.exec(get_subscription)).first()
    if not user_subscription:
        raise HTTPException(status_code=403, detail="No active subscription found")
    else:
        result = SubscriptionResponse(
            subscription_id=str(user_subscription.id),
            status=user_subscription.status,
            started_at=user_subscription.started_at.strftime('%H:%M:%S %d.%m.%Y'),
            expires_at=user_subscription.expires_at.strftime('%H:%M:%S %d.%m.%Y'),
            tier_name=user_subscription.tier.name,
            tier_name_ru=user_subscription.tier.name_ru,
            tier_description=user_subscription.tier.description,
            tier_description_ru=user_subscription.tier.description_ru
        )
        return result


@user_subscription.post("/downgrade")
async def downgrade_subscription(
        session: AsyncSession = Depends(get_session),
        user=Depends(get_current_user)
):
    """
    Cancels auto-renewal by removing the default payment method.
    The user remains on the current tier until expires_at, then the background job moves them to Free.
    """
    # Check if the user actually has a paid subscription
    get_subscription = select(subscription_tiers.UserSubscription).where(
        user.id == subscription_tiers.UserSubscription.user_id,
        subscription_tiers.UserSubscription.status == "active"
    ).options(selectinload(subscription_tiers.UserSubscription.tier))

    user_subscription = (await session.exec(get_subscription)).first()

    if not user_subscription:
        raise HTTPException(status_code=400, detail="No active subscription")

    if user_subscription.tier.price_cents == 0:
        raise HTTPException(status_code=400, detail="Already on Free tier")

    # Remove Default Payment Method to prevent future charges
    payment_methods = await session.exec(
        select(PaymentMethod).where(
            PaymentMethod.user_id == user.id,
            PaymentMethod.is_default == True
        )
    )

    for pm in payment_methods.all():
        await session.delete(pm)

    await session.commit()

    return {"status": "success", "message": "Subscription will be downgraded at the end of the billing period"}