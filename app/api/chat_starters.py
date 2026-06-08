from fastapi import APIRouter, Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api import chat_starters_helpers
from app.db.database import get_session
from app.schemas.chat_starters import (
    ChatStarterSuggestionsRequest,
    ChatStarterSuggestionsResponse,
)

chat_starters = APIRouter(tags=["chat-starters"], prefix="/chat-starters")


@chat_starters.get("", response_model=ChatStarterSuggestionsResponse)
async def get_chat_starters(
    request: ChatStarterSuggestionsRequest = Depends(),
    session: AsyncSession = Depends(get_session),
):
    return await chat_starters_helpers.get_chat_starter_suggestions(
        session=session,
        language=request.language,
        count=request.count,
    )
