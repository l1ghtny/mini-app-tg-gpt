import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api import usage_pack_helpers
from app.api.dependencies import get_current_user
from app.db.database import get_session
from app.schemas.subscriptions import UsagePackResponse

usage_packs = APIRouter(tags=["usage packs"], prefix="/usage-packs")


@usage_packs.get("", response_model=List[UsagePackResponse])
async def get_usage_packs(
    user=Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return await usage_pack_helpers.list_public_packs(session)


@usage_packs.get("/{pack_id}", response_model=UsagePackResponse)
async def get_usage_pack(
    pack_id: uuid.UUID,
    user=Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return await usage_pack_helpers.get_pack(session, pack_id)
