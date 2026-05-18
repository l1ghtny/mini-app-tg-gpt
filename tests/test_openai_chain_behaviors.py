import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

import app.api.chat_helpers as chat_helpers
import app.services.openai_service as openai_service
from app.db.models import AppUser, Conversation, Message
from app.schemas.chat import EditMessageRequest
from app.services.openai_chain import (
    resolve_previous_response_id_for_chain,
)


@pytest.mark.asyncio
async def test_delete_message_invalidates_chain_state():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=731000001)
        session.add(user)
        await session.commit()
        await session.refresh(user)

        conversation = Conversation(
            user_id=user.id,
            title="Delete chain reset",
            last_openai_response_id="resp_1",
            openai_chain_updated_at=chat_helpers.datetime.now(chat_helpers.timezone.utc).replace(tzinfo=None),
            openai_chain_context_fingerprint="fp_1",
        )
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)

        user_msg = Message(conversation_id=conversation.id, role="user")
        assistant_msg = Message(conversation_id=conversation.id, role="assistant")
        session.add(user_msg)
        session.add(assistant_msg)
        await session.commit()
        await session.refresh(user_msg)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        await chat_helpers.handle_delete_message(
            conversation_id=conversation.id,
            message_id=user_msg.id,
            session=session,
            current_user=user,
        )

    async with AsyncSession(engine, expire_on_commit=False) as session:
        updated = await session.get(Conversation, conversation.id)
        assert updated is not None
        assert updated.last_openai_response_id is None
        assert updated.openai_chain_updated_at is None
        assert updated.openai_chain_context_fingerprint is None


@pytest.mark.asyncio
async def test_edit_message_invalidates_chain_state():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=731000002)
        session.add(user)
        await session.commit()
        await session.refresh(user)

        conversation = Conversation(
            user_id=user.id,
            title="Edit chain reset",
            last_openai_response_id="resp_2",
            openai_chain_updated_at=chat_helpers.datetime.now(chat_helpers.timezone.utc).replace(tzinfo=None),
            openai_chain_context_fingerprint="fp_2",
        )
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)

        user_msg = Message(conversation_id=conversation.id, role="user")
        assistant_msg = Message(conversation_id=conversation.id, role="assistant")
        session.add(user_msg)
        session.add(assistant_msg)
        await session.commit()
        await session.refresh(user_msg)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        await chat_helpers.handle_edit_message(
            conversation_id=conversation.id,
            message_id=user_msg.id,
            request=EditMessageRequest(content="Edited text", images=[]),
            session=session,
            current_user=user,
        )

    async with AsyncSession(engine, expire_on_commit=False) as session:
        updated = await session.get(Conversation, conversation.id)
        assert updated is not None
        assert updated.last_openai_response_id is None
        assert updated.openai_chain_updated_at is None
        assert updated.openai_chain_context_fingerprint is None


def test_resolve_previous_response_id_detects_fingerprint_mismatch():
    conversation = Conversation(
        user_id=uuid.uuid4(),
        last_openai_response_id="resp_3",
        openai_chain_updated_at=chat_helpers.datetime.now(chat_helpers.timezone.utc).replace(tzinfo=None),
        openai_chain_context_fingerprint="stored_fp",
    )
    response_id, reason = resolve_previous_response_id_for_chain(
        conversation,
        current_fingerprint="different_fp",
        chaining_enabled=True,
        max_inactivity_days=14,
    )
    assert response_id is None
    assert reason == "context_fingerprint_mismatch"


@pytest.mark.asyncio
async def test_stream_falls_back_to_full_history_when_previous_response_rejected(monkeypatch):
    create_calls: list[dict] = []
    tracked_events: list[tuple[str, dict]] = []

    class _FakeStream:
        def __init__(self):
            usage = SimpleNamespace(input_tokens=0, output_tokens=0, output_tokens_details=None)
            response = SimpleNamespace(id="resp_ok", usage=usage)
            self._events = [SimpleNamespace(type="response.completed", response=response, sequence_number=1)]
            self._idx = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._idx >= len(self._events):
                raise StopAsyncIteration
            event = self._events[self._idx]
            self._idx += 1
            return event

    class _FakeResponses:
        def __init__(self):
            self.calls = 0

        async def create(self, **kwargs):
            self.calls += 1
            create_calls.append(kwargs)
            if self.calls == 1:
                raise Exception("invalid previous_response_id")
            return _FakeStream()

    class _FakeClient:
        def __init__(self):
            self.responses = _FakeResponses()

    class _DummyAsyncSession:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def _noop_log_usage(*_args, **_kwargs):
        return None

    def _capture_track_event(key: str, _user_id: str, tags: dict | None = None):
        tracked_events.append((key, tags or {}))

    monkeypatch.setattr(openai_service, "client", _FakeClient(), raising=True)
    monkeypatch.setattr(openai_service, "AsyncSession", _DummyAsyncSession, raising=True)
    monkeypatch.setattr(openai_service, "log_usage", _noop_log_usage, raising=True)
    monkeypatch.setattr(openai_service, "track_event", _capture_track_event, raising=True)

    current_turn = [{"role": "user", "content": [{"type": "input_text", "text": "latest turn"}]}]
    full_history = [{"role": "user", "content": [{"type": "input_text", "text": "full history"}]}]

    events = []
    async for ev in openai_service.stream_normalized_openai_response(
        current_turn,
        model="gpt-5.4-nano",
        previous_response_id="resp_old",
        fallback_messages=full_history,
        user_id=uuid.uuid4(),
    ):
        events.append(ev)

    assert len(create_calls) == 2
    assert create_calls[0].get("previous_response_id") == "resp_old"
    assert create_calls[0].get("input") == current_turn
    assert create_calls[1].get("previous_response_id") is None
    assert create_calls[1].get("input") == full_history
    assert any(e.get("type") == "done" for e in events)
    assert any(
        key == "openai.chain.fallback" and tags.get("reason") == "create_rejected_previous_response_id"
        for key, tags in tracked_events
    )
