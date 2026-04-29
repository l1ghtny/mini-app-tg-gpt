import os
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.chat_helpers import handle_delete_message, handle_edit_message
from app.db import models as m
from app.schemas.chat import EditMessageRequest


async def _seed_conversation_with_messages(session: AsyncSession, user_id: uuid.UUID):
    conversation = m.Conversation(title=f"conv-{uuid.uuid4()}", user_id=user_id)
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)

    user_one = m.Message(conversation_id=conversation.id, role="user")
    assistant_one = m.Message(conversation_id=conversation.id, role="assistant")
    user_two = m.Message(conversation_id=conversation.id, role="user")
    assistant_two = m.Message(conversation_id=conversation.id, role="assistant")
    session.add_all([user_one, assistant_one, user_two, assistant_two])
    await session.commit()
    await session.refresh(user_one)
    await session.refresh(assistant_one)
    await session.refresh(user_two)
    await session.refresh(assistant_two)

    session.add(m.MessageContent(message_id=user_one.id, ordinal=0, type="text", value="first"))
    session.add(m.MessageContent(message_id=assistant_one.id, ordinal=0, type="text", value="reply"))
    session.add(m.MessageContent(message_id=user_two.id, ordinal=0, type="text", value="second"))
    session.add(m.MessageContent(message_id=assistant_two.id, ordinal=0, type="text", value="reply2"))
    await session.commit()

    return conversation, user_one, assistant_one, user_two, assistant_two


@pytest.mark.asyncio
async def test_delete_message_removes_target_and_tail():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url

    engine = create_async_engine(test_db_url, future=True, echo=False)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        owner = m.AppUser(telegram_id=721000030)
        session.add(owner)
        await session.commit()
        await session.refresh(owner)
        conversation, user_one, assistant_one, _, _ = await _seed_conversation_with_messages(session, owner.id)

        await handle_delete_message(
            conversation_id=conversation.id,
            message_id=assistant_one.id,
            session=session,
            current_user=owner,
        )

    async with AsyncSession(engine, expire_on_commit=False) as session:
        remaining = (
            await session.exec(
                select(m.Message)
                .where(m.Message.conversation_id == conversation.id)
                .order_by(m.Message.created_at.asc(), m.Message.id.asc())
            )
        ).all()
        assert [message.id for message in remaining] == [user_one.id]


@pytest.mark.asyncio
async def test_edit_message_updates_content_and_truncates_tail():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url

    engine = create_async_engine(test_db_url, future=True, echo=False)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        owner = m.AppUser(telegram_id=721000031)
        session.add(owner)
        await session.commit()
        await session.refresh(owner)
        conversation, user_one, assistant_one, user_two, _ = await _seed_conversation_with_messages(session, owner.id)

        response = await handle_edit_message(
            conversation_id=conversation.id,
            message_id=user_two.id,
            request=EditMessageRequest(
                content="edited user message",
                images=["https://cdn.example/new.png"],
            ),
            session=session,
            current_user=owner,
        )

    async with AsyncSession(engine, expire_on_commit=False) as session:
        remaining = (
            await session.exec(
                select(m.Message)
                .where(m.Message.conversation_id == conversation.id)
                .order_by(m.Message.created_at.asc(), m.Message.id.asc())
            )
        ).all()
        assert [message.id for message in remaining] == [user_one.id, assistant_one.id, user_two.id]

        content_rows = (
            await session.exec(
                select(m.MessageContent)
                .where(m.MessageContent.message_id == user_two.id)
                .order_by(m.MessageContent.ordinal.asc())
            )
        ).all()
        assert [(row.type, row.value) for row in content_rows] == [
            ("text", "edited user message"),
            ("image_url", "https://cdn.example/new.png"),
        ]

    assert response.message_id == user_two.id
    assert response.deleted_after == 1


@pytest.mark.asyncio
async def test_edit_message_rejects_assistant_message():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url

    engine = create_async_engine(test_db_url, future=True, echo=False)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        owner = m.AppUser(telegram_id=721000032)
        session.add(owner)
        await session.commit()
        await session.refresh(owner)
        conversation, _, assistant_one, _, _ = await _seed_conversation_with_messages(session, owner.id)

        with pytest.raises(HTTPException) as exc:
            await handle_edit_message(
                conversation_id=conversation.id,
                message_id=assistant_one.id,
                request=EditMessageRequest(content="not allowed", images=None),
                session=session,
                current_user=owner,
            )
        assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_delete_message_enforces_owner():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url

    engine = create_async_engine(test_db_url, future=True, echo=False)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        owner = m.AppUser(telegram_id=721000033)
        other = m.AppUser(telegram_id=721000034)
        session.add(owner)
        session.add(other)
        await session.commit()
        await session.refresh(owner)
        await session.refresh(other)
        conversation, _, assistant_one, _, _ = await _seed_conversation_with_messages(session, owner.id)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        with pytest.raises(HTTPException) as exc:
            await handle_delete_message(
                conversation_id=conversation.id,
                message_id=assistant_one.id,
                session=session,
                current_user=other,
            )
        assert exc.value.status_code == 403
