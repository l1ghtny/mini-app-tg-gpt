from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import ChatStarterSuggestion
from app.schemas.chat_starters import (
    ChatStarterSuggestionResponse,
    ChatStarterSuggestionsResponse,
)


async def get_chat_starter_suggestions(
    *,
    session: AsyncSession,
    language: str,
    count: int,
) -> ChatStarterSuggestionsResponse:
    rows = (await session.exec(
        select(ChatStarterSuggestion)
        .where(
            ChatStarterSuggestion.is_active == True,
            ChatStarterSuggestion.language == language,
        )
        .order_by(func.random())
        .limit(count)
    )).all()

    return ChatStarterSuggestionsResponse(
        language=language,
        count=len(rows),
        items=[ChatStarterSuggestionResponse(text=row.text) for row in rows],
    )
