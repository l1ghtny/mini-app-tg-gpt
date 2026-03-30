import os
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.chat_helpers import handle_conversation_search, handle_get_conversation
from app.db.models import AppUser, Conversation


@pytest.mark.asyncio
async def test_get_conversation_enforces_owner():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url

    engine = create_async_engine(test_db_url, future=True, echo=False)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        owner = AppUser(telegram_id=721000010)
        other = AppUser(telegram_id=721000011)
        session.add(owner)
        session.add(other)
        await session.commit()
        await session.refresh(owner)
        await session.refresh(other)

        conv = Conversation(title=f"secret-{uuid.uuid4()}", user_id=owner.id)
        session.add(conv)
        await session.commit()
        await session.refresh(conv)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        with pytest.raises(HTTPException) as exc:
            await handle_get_conversation(
                conversation_id=conv.id,
                session=session,
                current_user=other,
            )
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_search_conversations_returns_only_current_user_rows():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url

    engine = create_async_engine(test_db_url, future=True, echo=False)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        owner = AppUser(telegram_id=721000020)
        other = AppUser(telegram_id=721000021)
        session.add(owner)
        session.add(other)
        await session.commit()
        await session.refresh(owner)
        await session.refresh(other)

        common = f"project-{uuid.uuid4()}"
        session.add(Conversation(title=common, user_id=owner.id))
        session.add(Conversation(title=common, user_id=other.id))
        await session.commit()

    async with AsyncSession(engine, expire_on_commit=False) as session:
        results = await handle_conversation_search(
            query="project-",
            session=session,
            current_user=owner,
        )

    assert len(results) == 1
    assert results[0].user_id == owner.id
