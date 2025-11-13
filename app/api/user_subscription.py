from fastapi import APIRouter, Depends
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.dependencies import get_current_user
from app.db import models, subscription_tiers
from app.db.database import get_session
from app.schemas.subscriptions import SubscriptionResponse

user_subscription = APIRouter(tags=['user/subscription'], prefix="/user/subscription")

@user_subscription.get("/active", response_model=SubscriptionResponse)
async def get_active_subscription(session: AsyncSession = Depends(get_session), user=Depends(get_current_user)):
    get_subscription = select(subscription_tiers.UserSubscription).where(user.id==subscription_tiers.UserSubscription.user_id, subscription_tiers.UserSubscription.status=="active").options(selectinload(subscription_tiers.UserSubscription.tier))
    user_subscription = (await session.exec(get_subscription)).first()
    print(type(user_subscription.discount_expires_at))
    if not user_subscription:
        return {"status": "none"}
    else:
        result = SubscriptionResponse(
            subscription_id=str(user_subscription.id),
            status=user_subscription.status,
            started_at=user_subscription.started_at.strftime('%H:%M:%S %d.%m.%Y'),
            expires_at=user_subscription.expires_at.strftime('%H:%M:%S %d.%m.%Y'),
            discount_percent=user_subscription.discount_percent,
            discount_expires_at=user_subscription.discount_expires_at.strftime('%H:%M:%S %d.%m.%Y'),
            tier_name=user_subscription.tier.name,
            tier_description=user_subscription.tier.description,
        )
        return result