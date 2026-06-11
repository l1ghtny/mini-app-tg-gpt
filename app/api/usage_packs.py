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
from app.services.subscription_check.entitlements import (
    get_active_usage_packs,
    get_pack_image_usage_sums,
    get_pack_usage_counts,
)

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
    if not active_packs:
        return UsageBalanceResponse(
            active_packs_count=0,
            label="No Active Packs",
            packs=[]
        )

    pack_ids = [user_pack.id for user_pack in active_packs]
    text_model_names = sorted(
        {
            limit.model_name
            for user_pack in active_packs
            for limit in user_pack.pack.pack_model_limits
        }
    )
    image_model_names = sorted(
        {
            limit.image_model
            for user_pack in active_packs
            for limit in user_pack.pack.pack_image_model_limits
        }
    )
    text_usage_map = await get_pack_usage_counts(session, current_user.id, pack_ids, text_model_names)
    image_usage_map = await get_pack_image_usage_sums(session, current_user.id, pack_ids, image_model_names)

    packs_info = []

    for user_pack in active_packs:
        pack_purchased = 0.0
        pack_remaining = 0.0
        
        # Sum up image credits
        for limit in user_pack.pack.pack_image_model_limits:
            cap = float(limit.credit_amount or 0)
            pack_purchased += cap
            used = float(image_usage_map.get((user_pack.id, limit.image_model), 0.0) or 0.0)
            rem = -1 if cap == -1 else max(0.0, cap - used)
            pack_remaining += rem

        # Sum up text credits (requests)
        for limit in user_pack.pack.pack_model_limits:
            cap = float(limit.request_credits or 0)
            pack_purchased += cap
            used = float(text_usage_map.get((user_pack.id, limit.model_name), 0) or 0)
            rem = -1 if cap == -1 else max(0.0, cap - used)
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
