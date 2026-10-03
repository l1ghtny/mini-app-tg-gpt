import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import (
    AppUser,
    ChatFolder,
    Conversation,
    ConversationSearchChunk,
    Message,
    MessageContent,
)
from app.services import conversation_search as search


class Embedder(search.ConversationSearchEmbedder):
    def embed_query(self, text):
        return [1.0, 0.0]

    def embed_passages(self, texts):
        return [[1.0, 0.0] for _ in texts]


async def add_passage(
    session, conversation, text, *, indexed_text=None, role="assistant"
):
    message = Message(conversation_id=conversation.id, role=role)
    session.add(message)
    await session.flush()
    content = MessageContent(message_id=message.id, type="text", ordinal=0, value=text)
    session.add(content)
    await session.flush()
    indexed = indexed_text if indexed_text is not None else search._normalize_text(text)
    chunk = ConversationSearchChunk(
        user_id=conversation.user_id,
        conversation_id=conversation.id,
        message_id=message.id,
        message_content_id=content.id,
        message_role=role,
        chunk_text=indexed,
        text_hash=search._hash_text(indexed),
        embedding=[1.0, 0.0],
    )
    session.add(chunk)
    await session.flush()
    return message, content, chunk


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query", ["Deploy", "ПЛАН РЕЛИЗА", "A very long release title " * 12]
)
async def test_exact_titles_precede_semantic_prefix_and_keep_duplicates(
    monkeypatch, query
):
    monkeypatch.setattr(search, "get_conversation_search_embedder", lambda: Embedder())
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    async with AsyncSession(engine, expire_on_commit=False) as session:
        owner = AppUser(telegram_id=730001001)
        session.add(owner)
        await session.flush()
        exact = [
            Conversation(
                user_id=owner.id,
                title=query.strip().lower(),
                updated_at=datetime(2020, 1, 1),
            )
            for _ in range(2)
        ]
        prefix = Conversation(
            user_id=owner.id,
            title=query.strip() + " notes",
            updated_at=datetime(2026, 1, 1),
        )
        for conversation in [*exact, prefix]:
            session.add(conversation)
        await session.flush()
        await add_passage(session, prefix, "A strongly matching semantic passage.")
        results = await search.search_conversations(
            session, current_user=owner, query=query
        )
        assert {result.id for result in results[:2]} == {
            conversation.id for conversation in exact
        }
        assert results[2].id == prefix.id
    await engine.dispose()


@pytest.mark.asyncio
async def test_excerpts_use_live_owned_messages_and_preserve_legacy_fields(monkeypatch):
    monkeypatch.setattr(search, "get_conversation_search_embedder", lambda: Embedder())
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    async with AsyncSession(engine, expire_on_commit=False) as session:
        owner, other = AppUser(telegram_id=730001002), AppUser(telegram_id=730001003)
        session.add(owner)
        session.add(other)
        await session.flush()
        folder = ChatFolder(user_id=owner.id, name="Delivery project")
        session.add(folder)
        await session.flush()
        valid = Conversation(
            user_id=owner.id,
            folder_id=folder.id,
            title="Infra notes",
            model="gpt-5.6-luna",
        )
        stale = Conversation(
            user_id=owner.id,
            title="Rewritten notes",
            history_summary="Do not expose this filler summary",
        )
        private = Conversation(user_id=other.id, title="Other account")
        deleted = Conversation(user_id=owner.id, title="Deploy removed passage")
        forged = Conversation(user_id=owner.id, title="Deploy forged index")
        system = Conversation(user_id=owner.id, title="Deploy system context")
        for conversation in [valid, stale, private, deleted, forged, system]:
            session.add(conversation)
        await session.flush()
        message, _, _ = await add_passage(
            session,
            valid,
            "## Deployment\n\n**Roll out** the release gradually. "
            + "Check the ready pods. " * 40,
        )
        await add_passage(
            session,
            stale,
            "This message has been rewritten.",
            indexed_text="Old deployment secret",
        )
        _, _, private_chunk = await add_passage(
            session, private, "Private deployment secret"
        )
        removed, _, _ = await add_passage(session, deleted, "Deleted deployment secret")
        await session.delete(removed)
        # Corrupt stale index ownership cannot authorize another account's text.
        _, _, forged_chunk = await add_passage(session, forged, "Visible ordinary text")
        forged_chunk.message_id = private_chunk.message_id
        forged_chunk.message_content_id = private_chunk.message_content_id
        forged_chunk.chunk_ordinal = 99
        forged_chunk.chunk_text = private_chunk.chunk_text
        forged_chunk.text_hash = private_chunk.text_hash
        await add_passage(
            session, system, "Hidden deployment system context", role="system"
        )
        await session.flush()
        results = await search.search_conversations(
            session, current_user=owner, query="deploy", include_metadata=True
        )
        by_id = {result.id: result for result in results}
        assert private.id not in by_id
        metadata = by_id[valid.id].search
        assert metadata.message_id == message.id
        assert metadata.message_created_at == message.created_at
        assert metadata.folder_name == folder.name
        assert "Roll out" in metadata.excerpt and "**" not in metadata.excerpt
        assert len(metadata.excerpt) <= 240
        for key, value in valid.model_dump().items():
            assert by_id[valid.id].model_dump()[key] == value
        for conversation in [stale, deleted, forged, system]:
            assert by_id[conversation.id].search.excerpt is None
        # Live conversation ownership is authoritative even while old index rows remain.
        valid.user_id = other.id
        await session.flush()
        again = await search.search_conversations(
            session, current_user=owner, query="deploy", include_metadata=True
        )
        assert valid.id not in {result.id for result in again}
    await engine.dispose()


@pytest.mark.asyncio
async def test_semantic_excerpt_has_no_invented_query_and_nonexact_order_is_unchanged(
    monkeypatch,
):
    monkeypatch.setattr(search, "get_conversation_search_embedder", lambda: Embedder())
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    async with AsyncSession(engine, expire_on_commit=False) as session:
        owner = AppUser(telegram_id=730001004)
        session.add(owner)
        await session.flush()
        conversations = [
            Conversation(
                user_id=owner.id,
                title=f"Notes {index}",
                updated_at=datetime(2026, 1, 1) + timedelta(days=index),
            )
            for index in range(3)
        ]
        for conversation in conversations:
            session.add(conversation)
        await session.flush()
        for conversation in conversations:
            for _ in range(8):
                await add_passage(session, conversation, "Bring the new version online gradually and check readiness.")
        legacy = await search.search_conversations(
            session, current_user=owner, query="release strategy"
        )
        class CountingSession:
            statements = []

            async def exec(self, statement):
                self.statements.append(statement)
                return await session.exec(statement)

        counted = CountingSession()
        enriched = await search.search_conversations(counted, current_user=owner,
                                                    query="release strategy", include_metadata=True)
        # Three result chats with 24 indexed passages still use bounded batch
        # lookups instead of fetching each conversation's full message history.
        assert len(counted.statements) <= 6
        bound_lists = [value for statement in counted.statements for value in statement.compile().params.values() if isinstance(value, list)]
        assert max(map(len, bound_lists)) <= 9
        assert (
            [item.id for item in enriched]
            == [item.id for item in legacy]
            == [item.id for item in reversed(conversations)]
        )
        assert all("release strategy" not in item.search.excerpt for item in enriched)
    await engine.dispose()


def test_plain_excerpt_is_bounded_and_keeps_literal_unicode_text():
    assert "uvicorn main:app --reload" in search._plain_excerpt("```python uvicorn main:app --reload ```", "uvicorn")
    for query in ["релиз", "a+b[0]", "absent"]:
        excerpt = search._plain_excerpt(
            "**Начало** " + "длинный текст " * 100 + query + " конец", query
        )
        assert len(excerpt) <= 240
        assert query in excerpt
        assert "**" not in excerpt
