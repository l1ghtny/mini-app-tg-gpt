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
