import os
import uuid

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api import chat_helpers
from app.db import models as m


@pytest.mark.asyncio
async def test_history_includes_assistant_images(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url

    engine = create_async_engine(test_db_url, future=True, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = m.AppUser(telegram_id=721000200)
        session.add(user)
        await session.commit()
        await session.refresh(user)

        conversation = m.Conversation(user_id=user.id, title=f"conv-{uuid.uuid4()}")
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)

        user_message = m.Message(conversation_id=conversation.id, role="user")
        assistant_message = m.Message(conversation_id=conversation.id, role="assistant")
        empty_assistant_message = m.Message(conversation_id=conversation.id, role="assistant")
        session.add(user_message)
        session.add(assistant_message)
        session.add(empty_assistant_message)
        await session.commit()
        await session.refresh(user_message)
        await session.refresh(assistant_message)
        await session.refresh(empty_assistant_message)

        session.add(m.MessageContent(message_id=user_message.id, type="text", value="draw a cat"))
        session.add(m.MessageContent(message_id=assistant_message.id, type="image_url", value="https://cdn.example/cat.png"))
        await session.commit()

        async def fake_ensure_image_url(_session, url, max_size=2048):
            return url

        monkeypatch.setattr(chat_helpers, "ensure_openai_compatible_image_url", fake_ensure_image_url, raising=True)

        history = await chat_helpers._build_history_for_openai(session, conversation.id)

    assert history == [
        {"role": "user", "content": [{"type": "input_text", "text": "draw a cat"}]},
        {"role": "assistant", "content": [{"type": "output_text", "text": "[Generated an image.]"}]},
    ]


@pytest.mark.asyncio
async def test_history_sliding_window_adds_summary_when_over_budget(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url

    engine = create_async_engine(test_db_url, future=True, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = m.AppUser(telegram_id=721000201)
        session.add(user)
        await session.commit()
        await session.refresh(user)

        conversation = m.Conversation(user_id=user.id, title=f"conv-{uuid.uuid4()}")
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)

        text_block = "lorem ipsum " * 80
        latest_user_text = "this should stay in recent window"

        for idx in range(40):
            user_msg = m.Message(conversation_id=conversation.id, role="user")
            assistant_msg = m.Message(conversation_id=conversation.id, role="assistant")
            session.add(user_msg)
            session.add(assistant_msg)
            await session.commit()
            await session.refresh(user_msg)
            await session.refresh(assistant_msg)

            user_text = latest_user_text if idx == 39 else f"user-{idx} {text_block}"
            assistant_text = f"assistant-{idx} {text_block}"
            session.add(m.MessageContent(message_id=user_msg.id, ordinal=0, type="text", value=user_text))
            session.add(m.MessageContent(message_id=assistant_msg.id, ordinal=0, type="text", value=assistant_text))
            await session.commit()

        async def fake_ensure_image_url(_session, url, max_size=2048):
            return url

        async def fake_summarize_history_chunk(**kwargs):
            return "summary: prior turns compressed"

        monkeypatch.setattr(chat_helpers, "ensure_openai_compatible_image_url", fake_ensure_image_url, raising=True)
        monkeypatch.setattr(chat_helpers, "summarize_history_chunk", fake_summarize_history_chunk, raising=True)

        history = await chat_helpers._build_history_for_openai(
            session,
            conversation.id,
            model_name="gpt-5.4-nano",
        )
        await session.refresh(conversation)

    assert history[0]["role"] == "system"
    assert "summary: prior turns compressed" in history[0]["content"][0]["text"]
    assert any(
        part.get("text") == latest_user_text
        for message in history
        for part in message.get("content", [])
        if part.get("type") in {"input_text", "output_text"}
    )
    assert conversation.history_summary == "summary: prior turns compressed"
    assert conversation.history_summary_up_to_message_id is not None


@pytest.mark.asyncio
async def test_history_small_dialogue_has_no_summary(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url

    engine = create_async_engine(test_db_url, future=True, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = m.AppUser(telegram_id=721000202)
        session.add(user)
        await session.commit()
        await session.refresh(user)

        conversation = m.Conversation(user_id=user.id, title=f"conv-{uuid.uuid4()}")
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)

        user_message = m.Message(conversation_id=conversation.id, role="user")
        assistant_message = m.Message(conversation_id=conversation.id, role="assistant")
        session.add(user_message)
        session.add(assistant_message)
        await session.commit()
        await session.refresh(user_message)
        await session.refresh(assistant_message)

        session.add(m.MessageContent(message_id=user_message.id, ordinal=0, type="text", value="hello"))
        session.add(m.MessageContent(message_id=assistant_message.id, ordinal=0, type="text", value="hi there"))
        await session.commit()

        async def fake_ensure_image_url(_session, url, max_size=2048):
            return url

        async def fail_if_called(**kwargs):
            raise AssertionError("summary model should not be called for small history")

        monkeypatch.setattr(chat_helpers, "ensure_openai_compatible_image_url", fake_ensure_image_url, raising=True)
        monkeypatch.setattr(chat_helpers, "summarize_history_chunk", fail_if_called, raising=True)

        history = await chat_helpers._build_history_for_openai(
            session,
            conversation.id,
            model_name="gpt-5.4-nano",
        )
        await session.refresh(conversation)

    assert history == [
        {"role": "user", "content": [{"type": "input_text", "text": "hello"}]},
        {"role": "assistant", "content": [{"type": "output_text", "text": "hi there"}]},
    ]
    assert conversation.history_summary is None
