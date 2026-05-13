from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api import whats_new_helpers
from app.api.dependencies import get_current_user
from app.db.database import get_session
from app.db.models import AppUser
from app.schemas.whats_new import WhatsNewListResponse, WhatsNewSeenRequest, WhatsNewSeenResponse

whats_new = APIRouter(tags=["whats-new"], prefix="/whats-new")


@whats_new.get("", response_model=WhatsNewListResponse)
async def get_whats_new(
    lang: str = Query(default="en"),
    since: datetime | None = Query(default=None),
    limit: int = Query(default=whats_new_helpers.DEFAULT_LIMIT, ge=1, le=whats_new_helpers.MAX_LIMIT),
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    return await whats_new_helpers.get_whats_new(
        session=session,
        current_user=current_user,
        lang=lang,
        since=since,
        limit=limit,
    )


@whats_new.post("/seen", response_model=WhatsNewSeenResponse)
async def mark_whats_new_seen(
    request: WhatsNewSeenRequest,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    return await whats_new_helpers.mark_whats_new_seen(
        session=session,
        current_user=current_user,
        up_to=request.up_to,
        ids=request.ids,
    )

