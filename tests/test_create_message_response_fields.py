import os
import uuid

import pytest
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

import app.api.chat_helpers as chat_helpers
from app.api.routes import create_message
from app.db.models import AppUser, Conversation
from app.schemas.chat import MessageContent, NewMessageRequest


class _DummyRedis:
    async def exists(self, *_args, **_kwargs):
        return True


@pytest.mark.asyncio
async def test_create_message_returns_user_and_assistant_ids(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=721000201)
        session.add(user)
        await session.commit()
        await session.refresh(user)

        conversation = Conversation(user_id=user.id, title="Route IDs")
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)

    async def _fake_check_entitlements(*_args, **_kwargs):
        return (
            chat_helpers.TextEntitlementSelection(remaining=10, tier_id=None, usage_pack_id=None),
            chat_helpers.ImageEntitlementSelection(
                allowed=True,
                tier_id=None,
                usage_pack_id=None,
                cost=1.0,
                throttle_reason=None,
                wait_time=None,
            ),
            [],
            "gpt-image-1.5",
            "low",
        )

    async def _fake_build_history(*_args, **_kwargs):
        return []

    def _fake_queue_generation(*_args, **_kwargs):
        return None

    async def _fake_track_metrics(*_args, **_kwargs):
        return None

    monkeypatch.setattr(chat_helpers, "_check_entitlements", _fake_check_entitlements)
    monkeypatch.setattr(chat_helpers, "_build_history_for_openai", _fake_build_history)
    monkeypatch.setattr(chat_helpers, "_queue_generation", _fake_queue_generation)
    monkeypatch.setattr(chat_helpers, "_track_message_metrics", _fake_track_metrics)

    request = NewMessageRequest(
        client_request_id=str(uuid.uuid4()),
        role="user",
        content=[MessageContent(type="text", value="hello")],
        model="gpt-5-nano",
        tool_choice="auto",
    )

    async with AsyncSession(engine, expire_on_commit=False) as session:
        response = await create_message(
            conversation_id=conversation.id,
            request=request,
            background_tasks=BackgroundTasks(),
            session=session,
            current_user=user,
            bus=_DummyRedis(),
            _rate_limit_ok=True,
        )

    assert response.user_message_id is not None
    assert response.assistant_message_id is not None
    assert response.message_id == response.assistant_message_id
    assert str(response.stream_url).endswith(f"/messages/{response.assistant_message_id}/stream")


@pytest.mark.asyncio
async def test_create_message_idempotent_response_includes_both_ids(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=721000202)
        session.add(user)
        await session.commit()
        await session.refresh(user)

        conversation = Conversation(user_id=user.id, title="Route Idempotency IDs")
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)

    async def _fake_check_entitlements(*_args, **_kwargs):
        return (
            chat_helpers.TextEntitlementSelection(remaining=10, tier_id=None, usage_pack_id=None),
            chat_helpers.ImageEntitlementSelection(
                allowed=True,
                tier_id=None,
                usage_pack_id=None,
                cost=1.0,
                throttle_reason=None,
                wait_time=None,
            ),
            [],
            "gpt-image-1.5",
            "low",
        )

    async def _fake_build_history(*_args, **_kwargs):
        return []

    def _fake_queue_generation(*_args, **_kwargs):
        return None

    async def _fake_track_metrics(*_args, **_kwargs):
        return None

    async def _fake_choose_link(_session, _bus, conversation_id, assistant_message_id, _created_at):
        return {
            "message_id": str(assistant_message_id),
            "stream_url": f"/api/v1/conversations/{conversation_id}/messages/{assistant_message_id}/stream",
            "messages_url": None,
        }

    monkeypatch.setattr(chat_helpers, "_check_entitlements", _fake_check_entitlements)
    monkeypatch.setattr(chat_helpers, "_build_history_for_openai", _fake_build_history)
    monkeypatch.setattr(chat_helpers, "_queue_generation", _fake_queue_generation)
    monkeypatch.setattr(chat_helpers, "_track_message_metrics", _fake_track_metrics)
    monkeypatch.setattr(chat_helpers, "_choose_link_for_message", _fake_choose_link)

    request_id = str(uuid.uuid4())
    request = NewMessageRequest(
        client_request_id=request_id,
        role="user",
        content=[MessageContent(type="text", value="hello")],
        model="gpt-5-nano",
        tool_choice="auto",
    )

    async with AsyncSession(engine, expire_on_commit=False) as session:
        first = await create_message(
            conversation_id=conversation.id,
            request=request,
            background_tasks=BackgroundTasks(),
            session=session,
            current_user=user,
            bus=_DummyRedis(),
            _rate_limit_ok=True,
        )

    async with AsyncSession(engine, expire_on_commit=False) as session:
        second = await create_message(
            conversation_id=conversation.id,
            request=request,
            background_tasks=BackgroundTasks(),
            session=session,
            current_user=user,
            bus=_DummyRedis(),
            _rate_limit_ok=True,
        )

    assert second.user_message_id == first.user_message_id
    assert second.assistant_message_id == first.assistant_message_id
    assert second.message_id == second.assistant_message_id
    assert str(second.stream_url).endswith(f"/messages/{second.assistant_message_id}/stream")
