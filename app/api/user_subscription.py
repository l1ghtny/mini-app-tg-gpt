from fastapi import APIRouter, BackgroundTasks, Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api import user_subscription_helpers
from app.api.dependencies import get_current_user
from app.db.database import get_session
from app.schemas.subscriptions import CancelSubscriptionResponse, SubscriptionResponse

user_subscription = APIRouter(tags=["user/subscription"], prefix="/user/subscription")


@user_subscription.get("/active", response_model=SubscriptionResponse)
async def get_active_subscription(
    session: AsyncSession = Depends(get_session),
    user=Depends(get_current_user),
):
    return await user_subscription_helpers.get_active_subscription(session, user)


@user_subscription.post("/cancel", response_model=CancelSubscriptionResponse)
async def cancel_subscription(
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    user=Depends(get_current_user),
):
    return await user_subscription_helpers.cancel_subscription(session, user, background_tasks)
