from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import delete
from sqlmodel import select

from app.api import chat_helpers as chat
from app.db.models import AppUser, ChatFolder, Conversation, Message, MessageContent, UserDocument, ConversationDocument
from app.schemas.chat import NewMessageRequest, EditMessageRequest, MessageContent as ContentResponse


@pytest.mark.asyncio
async def test_snapshot_survives_detach_rename_delete_and_reload_without_changing_model_input(db, monkeypatch):
    engine, session, user = db
    async with engine.begin() as conn:
        for model in (ChatFolder, Conversation, Message, MessageContent, UserDocument, ConversationDocument):
            await conn.run_sync(lambda c, m=model: m.__table__.create(c))
    monkeypatch.setattr(chat, 'queue_message_reindex', AsyncMock())
    monkeypatch.setattr(chat, 'queue_projection_refresh', AsyncMock())
    monkeypatch.setattr(chat, 'detach_assets_from_message_content_ids', AsyncMock())
    conversation = Conversation(user_id=user.id)
    other = AppUser()
    session.add_all([conversation, other])
    await session.flush()
    first = UserDocument(user_id=user.id, filename='Original.pdf', status='ready')
    foreign = UserDocument(user_id=other.id, filename='Private.pdf', status='ready')
    deleted = UserDocument(user_id=user.id, filename='Deleted.pdf', deleted_at=datetime.now())
    session.add_all([first, foreign, deleted])
    await session.flush()
    session.add_all([ConversationDocument(conversation_id=conversation.id, document_id=doc.id) for doc in (first, foreign, deleted)])
    await session.commit()
    request = NewMessageRequest(client_request_id='snapshot', model='gpt-5.6-terra', role='user', content=[
        {'type':'text','value':'Summarize this', 'data':{'attached_documents':[{'id':'fake','filename':'Spoofed'}]}},
        {'type':'text','value':'Keep it brief'},
    ])
    message = await chat._create_user_message(session, conversation, request, BackgroundTasks())
    await session.refresh(message, ['content'])
    expected = [{'id':str(first.id),'filename':'Original.pdf'}]
    parts = list(message.content)
    assert sum(p.data is not None for p in parts) == 1
    snapshot = next(p for p in parts if p.data)
    assert ContentResponse.model_validate(snapshot).data == {'attached_documents':expected}
    assert 'attached_documents' not in str(chat._build_history_candidate(message).payload)
    assert 'Original.pdf' not in str(chat._build_history_candidate(message).payload)
    first.filename = 'Renamed.pdf'
    first.deleted_at = datetime.now()
    session.add(first)
    await session.execute(delete(ConversationDocument).where(ConversationDocument.conversation_id == conversation.id))
    await session.commit()
    snapshot_id, conversation_id, user_id = snapshot.id, conversation.id, user.id
    session.expire_all()
    reloaded = await session.get(MessageContent, snapshot_id)
    assert reloaded.data == {'attached_documents':expected}
    conversation = await session.get(Conversation, conversation_id)
    empty = await chat._create_user_message(session, conversation, request, BackgroundTasks())
    await session.refresh(empty, ['content'])
    assert next(p for p in empty.content if p.data).data == {'attached_documents':[]}
    # An edit represents a new send using the current selection.
    await session.refresh(message)
    await session.refresh(message, ['content'])
    await chat._replace_message_content(session, message, EditMessageRequest(content='Edited'), user_id=user_id)
    await session.commit()
    edited = (await session.exec(select(MessageContent).where(MessageContent.message_id == message.id))).all()
    assert len(edited) == 1
    assert edited[0].data == {'attached_documents':[]}
