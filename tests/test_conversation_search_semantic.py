import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.chat_helpers import handle_conversation_search
from app.db.models import (
    AppUser,
    Conversation,
    ConversationSearchChunk,
    ConversationSearchProjection,
    Message,
    MessageContent,
)
from app.services import conversation_search


class FakeEmbedder(conversation_search.ConversationSearchEmbedder):
    def _embed(self, text: str) -> list[float]:
        lowered = text.lower()
        vector = [0.0, 0.0, 0.0, 0.0]
        if "uvicorn" in lowered or "reload" in lowered:
            vector[0] = 1.0
        if "borsch" in lowered or "beet" in lowered:
            vector[1] = 1.0
        if "deploy" in lowered or "release" in lowered:
            vector[2] = 1.0
        if sum(vector) == 0:
            vector[3] = 1.0
        return vector

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]


@pytest.mark.asyncio
async def test_search_conversations_matches_assistant_text(monkeypatch):
    monkeypatch.setattr(conversation_search, "get_conversation_search_embedder", lambda: FakeEmbedder())

    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        owner = AppUser(telegram_id=721111100)
        session.add(owner)
        await session.commit()
        await session.refresh(owner)

        conversation = Conversation(title="Infra notes", user_id=owner.id)
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)

        user_message = Message(conversation_id=conversation.id, role="user")
        assistant_message = Message(conversation_id=conversation.id, role="assistant")
        session.add(user_message)
        session.add(assistant_message)
        await session.commit()
        await session.refresh(user_message)
        await session.refresh(assistant_message)

        session.add(MessageContent(message_id=user_message.id, ordinal=0, type="text", value="How do I run the app?"))
        session.add(
            MessageContent(
                message_id=assistant_message.id,
                ordinal=0,
                type="text",
                value="Use uvicorn main:app --reload to start the server.",
            )
        )
        await session.commit()

        await conversation_search.reindex_conversation(session, conversation_id=conversation.id)
        await session.commit()

        results = await handle_conversation_search(
            query="uvicorn reload",
            session=session,
            current_user=owner,
        )

    assert [item.id for item in results] == [conversation.id]
    await engine.dispose()


@pytest.mark.asyncio
async def test_search_worker_processes_jobs_and_stores_user_scoped_rows(monkeypatch):
    monkeypatch.setattr(conversation_search, "get_conversation_search_embedder", lambda: FakeEmbedder())

    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        owner = AppUser(telegram_id=721111101)
        session.add(owner)
        await session.commit()
        await session.refresh(owner)

        conversation = Conversation(title="Recipe ideas", user_id=owner.id)
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)

        message = Message(conversation_id=conversation.id, role="assistant")
        session.add(message)
        await session.commit()
        await session.refresh(message)

        session.add(
            MessageContent(
                message_id=message.id,
                ordinal=0,
                type="text",
                value="Classic borsch needs beet, stock, and dill.",
            )
        )
        await session.commit()

        await conversation_search.queue_message_reindex(
            session,
            conversation_id=conversation.id,
            message_id=message.id,
        )
        await conversation_search.queue_projection_refresh(session, conversation_id=conversation.id)
        await session.commit()

    while await conversation_search.run_search_job_once():
        pass

    async with AsyncSession(engine, expire_on_commit=False) as session:
        chunks = (
            await session.exec(
                select(ConversationSearchChunk).where(
                    ConversationSearchChunk.conversation_id == conversation.id
                )
            )
        ).all()
        projection = (
            await session.exec(
                select(ConversationSearchProjection).where(
                    ConversationSearchProjection.conversation_id == conversation.id
                )
            )
        ).first()

    assert chunks
    assert all(row.user_id == owner.id for row in chunks)
    assert projection is not None
    assert projection.user_id == owner.id
    await engine.dispose()


@pytest.mark.asyncio
async def test_reindex_conversation_handles_null_message_timestamps(monkeypatch):
    monkeypatch.setattr(conversation_search, "get_conversation_search_embedder", lambda: FakeEmbedder())

    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        owner = AppUser(telegram_id=721111102)
        session.add(owner)
        await session.commit()
        await session.refresh(owner)

        conversation = Conversation(title="Legacy timestamps", user_id=owner.id)
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)

        older_message = Message(conversation_id=conversation.id, role="user")
        newer_message = Message(conversation_id=conversation.id, role="assistant")
        session.add(older_message)
        session.add(newer_message)
        await session.commit()
        await session.refresh(older_message)
        await session.refresh(newer_message)

        session.add(MessageContent(message_id=older_message.id, ordinal=0, type="text", value="release checklist"))
        session.add(MessageContent(message_id=newer_message.id, ordinal=0, type="text", value="deploy notes"))
        await session.commit()

        await session.execute(
            text("UPDATE message SET created_at = NULL WHERE id = :message_id"),
            {"message_id": str(older_message.id)},
        )
        await session.commit()

        await conversation_search.reindex_conversation(session, conversation_id=conversation.id)
        await session.commit()

        chunks = (
            await session.exec(
                select(ConversationSearchChunk).where(
                    ConversationSearchChunk.conversation_id == conversation.id
                )
            )
        ).all()
        projection = (
            await session.exec(
                select(ConversationSearchProjection).where(
                    ConversationSearchProjection.conversation_id == conversation.id
                )
            )
        ).first()

    assert len(chunks) == 2
    assert projection is not None
    await engine.dispose()
