import os
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api import chat_helpers
from app.db import models as m
from app.r2.settings import Settings
from app.services.background import image_deriver


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


@pytest.mark.asyncio
async def test_history_keeps_stored_user_image_url_when_openai_url_differs(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url

    engine = create_async_engine(test_db_url, future=True, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = m.AppUser(telegram_id=721000204)
        session.add(user)
        await session.commit()
        await session.refresh(user)

        conversation = m.Conversation(user_id=user.id, title=f"conv-{uuid.uuid4()}")
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)

        user_message = m.Message(conversation_id=conversation.id, role="user")
        session.add(user_message)
        await session.commit()
        await session.refresh(user_message)

        proxied_url = "https://lightny.ru/images/images/free/uploaded/2026/06/14/test.png"
        openai_url = "https://tg-bot-images.lightny.pro/images/free/uploaded/2026/06/14/test.png"

        session.add(m.MessageContent(message_id=user_message.id, ordinal=0, type="image_url", value=proxied_url))
        await session.commit()

        async def fake_ensure_image_url(_session, url, max_size=2048):
            assert url == proxied_url
            return openai_url

        monkeypatch.setattr(chat_helpers, "ensure_openai_compatible_image_url", fake_ensure_image_url, raising=True)

        history = await chat_helpers._build_history_for_openai(session, conversation.id)
        content = (
            await session.exec(
                select(m.MessageContent).where(m.MessageContent.message_id == user_message.id)
            )
        ).first()

    assert history == [{"role": "user", "content": [{"type": "input_image", "image_url": openai_url}]}]
    assert content is not None
    assert content.value == proxied_url


@pytest.mark.asyncio
async def test_history_rejects_processing_user_image_until_ready(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url

    engine = create_async_engine(test_db_url, future=True, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = m.AppUser(telegram_id=721000205)
        session.add(user)
        await session.commit()
        await session.refresh(user)

        conversation = m.Conversation(user_id=user.id, title=f"conv-{uuid.uuid4()}")
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)

        user_message = m.Message(conversation_id=conversation.id, role="user")
        session.add(user_message)
        await session.commit()
        await session.refresh(user_message)

        proxied_url = "https://lightny.ru/images/images/free/uploaded/2026/06/15/test.png"
        key = "images/free/uploaded/2026/06/15/test.png"
        content = m.MessageContent(message_id=user_message.id, ordinal=0, type="image_url", value=proxied_url)
        session.add(content)
        await session.commit()
        await session.refresh(content)

        session.add(
            m.ImageAsset(
                user_id=user.id,
                conversation_id=conversation.id,
                message_content_id=content.id,
                bucket="tg-bot-images",
                key=key,
                public_url=proxied_url,
                source="uploaded",
                retention_policy="free_30d",
                status="processing",
            )
        )
        await session.commit()

        monkeypatch.setattr(Settings, "R2_PUBLIC_BASE_URL", "https://lightny.ru/images/", raising=False)
        monkeypatch.setattr(Settings, "R2_OPENAI_PUBLIC_BASE_URL", "https://tg-bot-images.lightny.pro/", raising=False)

        async def _fake_refresh_processing_image_asset(_session, _asset, **_kwargs):
            return False

        monkeypatch.setattr(
            image_deriver,
            "refresh_processing_image_asset",
            _fake_refresh_processing_image_asset,
            raising=True,
        )

        with pytest.raises(HTTPException) as exc_info:
            await chat_helpers._build_history_for_openai(session, conversation.id)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "image_not_ready"


def test_resolve_system_prompt_includes_main_and_folder_prompts():
    user = m.AppUser(telegram_id=721000203, default_prompt="Use concise and practical wording.")
    conversation = m.Conversation(user_id=user.id, title="conv-test")
    folder = m.ChatFolder(user_id=user.id, name="Work", prompt="Focus on backend architecture details.")
    conversation.folder = folder

    resolved = chat_helpers._resolve_system_prompt(conversation, user)

    assert "Main user prompt:" in resolved
    assert "Use concise and practical wording." in resolved
    assert "Folder prompt for this chat:" in resolved
    assert "Focus on backend architecture details." in resolved
