import uuid
from typing import List

from fastapi import APIRouter, Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api import tier_helpers
from app.api.dependencies import get_current_user
from app.db.database import get_session
from app.schemas.subscriptions import SubscriptionTierResponse, TierSubscribeResponse

tiers = APIRouter(tags=["subscription tiers"], prefix="/tiers")


@tiers.get("", response_model=List[SubscriptionTierResponse])
async def get_tiers(
    user=Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await tier_helpers.list_public_tiers(session, user)


@tiers.get("/{tier_id}", response_model=SubscriptionTierResponse)
async def get_tier(
    tier_id: uuid.UUID,
    user=Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await tier_helpers.get_tier_detail(session, user, tier_id)


@tiers.post("/subscribe/{tier_id}", response_model=TierSubscribeResponse)
async def tier_subscribe(
    tier_id: uuid.UUID,
    user=Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await tier_helpers.subscribe_to_tier(session, user, tier_id)
