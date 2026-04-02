import uuid
from typing import List, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api import usage_pack_helpers
from app.api.dependencies import get_current_user
from app.db.database import get_session
from app.db.models import AppUser
from app.schemas.subscriptions import UsagePackResponse
from app.schemas.usage import UsageBalanceResponse, UsagePackBalanceInfo
from app.services.subscription_check.entitlements import get_active_usage_packs, remaining_pack_image_requests_for_model, remaining_pack_requests_for_model

usage_packs = APIRouter(tags=["usage packs"], prefix="/usage-packs")


@usage_packs.get("", response_model=List[UsagePackResponse])
async def get_usage_packs(
    user=Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return await usage_pack_helpers.list_public_packs(session)


@usage_packs.get("/balance", response_model=UsageBalanceResponse)
async def get_usage_balance(
        current_user: AppUser = Depends(get_current_user),
        session: AsyncSession = Depends(get_session),
) -> Any:
    """
    Get the aggregated usage balance from all purchased packs.
    """
    # 1. Fetch all active/non-expired packs for the user
    active_packs = await get_active_usage_packs(session, current_user.id)

    packs_info = []

    for user_pack in active_packs:
        pack_purchased = 0.0
        pack_remaining = 0.0
        
        # Sum up image credits
        for limit in user_pack.pack.pack_image_model_limits:
            pack_purchased += limit.credit_amount
            
            # Calculate remaining for this specific limit
            rem = await remaining_pack_image_requests_for_model(session, user_pack, limit.image_model)
            pack_remaining += rem

        # Sum up text credits (requests)
        for limit in user_pack.pack.pack_model_limits:
            pack_purchased += limit.request_credits
            
            # Calculate remaining for this specific limit
            rem = await remaining_pack_requests_for_model(session, user_pack, limit.model_name)
            pack_remaining += rem
            
        packs_info.append(UsagePackBalanceInfo(
            pack_id=str(user_pack.pack.id),
            name=user_pack.pack.name,
            total_credits=pack_purchased,
            used_credits=pack_purchased - pack_remaining,
            remaining_credits=pack_remaining,
            expires_at=user_pack.expires_at,
            purchased_at=user_pack.purchased_at,
            pack_details=usage_pack_helpers.pack_to_response(user_pack.pack)
        ))

    # If no packs, return zeros, frontend will handle hiding/empty state
    if not active_packs:
        return UsageBalanceResponse(
            active_packs_count=0,
            label="No Active Packs",
            packs=[]
        )

    return UsageBalanceResponse(
        active_packs_count=len(active_packs),
        label="Available Credits",
        packs=packs_info
    )


@usage_packs.get("/{pack_id}", response_model=UsagePackResponse)
async def get_usage_pack(
    pack_id: uuid.UUID,
    user=Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return await usage_pack_helpers.get_pack(session, pack_id)
