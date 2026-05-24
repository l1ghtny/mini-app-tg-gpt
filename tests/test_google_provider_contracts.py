import os
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.chat_helpers import (
    _resolve_image_settings,
    _validate_reasoning_controls,
    handle_update_conversation_settings,
)
from app.api.model_catalog_helpers import _normalize_supports
from app.db.models import AppUser, Conversation
from app.schemas.chat import MessageContent, NewMessageRequest, UpdateConversationSettingsRequest


def test_model_catalog_supports_includes_thinking_flag():
    supports = _normalize_supports({"web_search": True, "thinking": True})

    assert supports.web_search is True
    assert supports.thinking is True


def test_resolve_image_settings_aligns_provider_and_rejects_explicit_mismatch():
    conversation = Conversation(
        user_id=uuid.uuid4(),
        model="gpt-5.4-nano",
        image_model="gpt-image-1.5",
        image_quality="low",
        image_size="1k",
    )
    request = NewMessageRequest(
        client_request_id=str(uuid.uuid4()),
        role="user",
        content=[MessageContent(type="text", value="hello")],
        model="gemini-3.1-flash-lite",
        tool_choice="auto",
        image_size="1k",
    )

    image_model, image_quality, image_size = _resolve_image_settings(request, conversation, request.model)

    assert image_model == "gemini-2.5-flash-image"
    assert image_quality == ""
    assert image_size == "1k"

    mismatch_request = NewMessageRequest(
        client_request_id=str(uuid.uuid4()),
        role="user",
        content=[MessageContent(type="text", value="hello")],
        model="gemini-3.1-flash-lite",
        tool_choice="auto",
        image_model="gpt-image-1.5",
    )

    with pytest.raises(HTTPException) as exc_info:
        _resolve_image_settings(mismatch_request, conversation, mismatch_request.model)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"] == "provider_mismatch"


def test_openai_model_accepts_boolean_thinking_toggle_for_backward_compat():
    request = NewMessageRequest(
        client_request_id=str(uuid.uuid4()),
        role="user",
        content=[MessageContent(type="text", value="hello")],
        model="gpt-5.4-nano",
        tool_choice="auto",
        thinking=True,
    )

    _validate_reasoning_controls(request)


@pytest.mark.asyncio
async def test_update_conversation_settings_swaps_image_model_with_text_provider_change():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=721000301)
        session.add(user)
        await session.commit()
        await session.refresh(user)

        conversation = Conversation(
            user_id=user.id,
            title="Provider swap",
            model="gpt-5.4-nano",
            image_model="gpt-image-1.5",
        )
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)

        updated = await handle_update_conversation_settings(
            conversation_id=conversation.id,
            request=UpdateConversationSettingsRequest(model="gemini-3.1-flash-lite"),
            session=session,
            current_user=user,
        )

    assert updated.model == "gemini-3.1-flash-lite"
    assert updated.image_model == "gemini-2.5-flash-image"
