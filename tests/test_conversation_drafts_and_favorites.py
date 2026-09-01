import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.api import chat_helpers
from app.db.models import AppUser, Conversation
from app.schemas.chat import (
    MessageContent,
    NewMessageRequest,
    UpdateConversationDraftRequest,
    UpdateConversationFavoriteRequest,
)


def _session() -> SimpleNamespace:
    return SimpleNamespace(
        add=Mock(),
        flush=AsyncMock(),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_updates_owned_conversation_draft_and_favorite(monkeypatch):
    user = AppUser(id=uuid.uuid4(), telegram_id=721000401)
    conversation = Conversation(user_id=user.id, title="Feedback")
    session = _session()
    monkeypatch.setattr(
        chat_helpers,
        "_load_conversation_for_user",
        AsyncMock(return_value=conversation),
    )

    saved_text, saved_at = await chat_helpers.handle_update_conversation_draft(
        conversation_id=conversation.id,
        request=UpdateConversationDraftRequest(content="Keep this reply"),
        session=session,
        current_user=user,
    )
    favorite = await chat_helpers.handle_update_conversation_favorite(
        conversation_id=conversation.id,
        request=UpdateConversationFavoriteRequest(is_favorite=True),
        session=session,
        current_user=user,
    )

    assert saved_text == "Keep this reply"
    assert conversation.draft_text == saved_text
    assert conversation.draft_updated_at == saved_at
    assert favorite.is_favorite is True
    assert favorite.favorited_at is not None
    assert session.commit.await_count == 2


@pytest.mark.asyncio
async def test_accepted_user_message_clears_saved_draft(monkeypatch):
    user_id = uuid.uuid4()
    conversation = Conversation(
        user_id=user_id,
        title="Feedback",
        draft_text="Send this",
    )
    session = _session()
    monkeypatch.setattr(chat_helpers, "queue_message_reindex", AsyncMock())
    monkeypatch.setattr(chat_helpers, "queue_projection_refresh", AsyncMock())
    request = NewMessageRequest(
        client_request_id=str(uuid.uuid4()),
        role="user",
        content=[MessageContent(type="text", value="Send this")],
        model="gpt-5.4-nano",
    )

    await chat_helpers._create_user_message(
        session,
        conversation,
        request,
        SimpleNamespace(add_task=Mock()),
    )

    assert conversation.draft_text is None
    assert conversation.draft_updated_at == conversation.updated_at
