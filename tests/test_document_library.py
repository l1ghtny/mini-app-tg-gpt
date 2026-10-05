import uuid
from unittest.mock import AsyncMock

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession
from app.api import document_helpers as documents
from app.db.database import engine
from app.db.models import AppUser, ChatFolder, ChatFolderDocument, Conversation, ConversationDocument, UserDocument


@pytest.mark.asyncio
async def test_library_preserves_names_and_scopes_usage_context_to_owner(monkeypatch):
    async with AsyncSession(engine, expire_on_commit=False) as session:
        owner = AppUser(telegram_id=12340001)
        other = AppUser(telegram_id=12340002)
        session.add_all([owner, other])
        await session.flush()
        file = UserDocument(user_id=owner.id, filename="internal-owner-20261005.pdf", original_filename="Project brief.pdf", status="ready", provider_artifacts=[])
        foreign_file = UserDocument(user_id=other.id, filename="Private.pdf", status="ready", provider_artifacts=[])
        chat = Conversation(user_id=owner.id, title="Research")
        foreign_chat = Conversation(user_id=other.id, title="Secret chat")
        project = ChatFolder(user_id=owner.id, name="Launch")
        session.add_all([file, foreign_file, chat, foreign_chat, project])
        await session.flush()
        session.add_all([
            ConversationDocument(conversation_id=chat.id, document_id=file.id),
            ConversationDocument(conversation_id=foreign_chat.id, document_id=file.id),
            ChatFolderDocument(folder_id=project.id, document_id=file.id),
        ])
        await session.commit()
        result = await documents.list_documents(session, owner)
        assert len(result.documents) == 1
        response = result.documents[0]
        assert response.filename == "internal-owner-20261005.pdf"
        assert response.original_filename == "Project brief.pdf"
        assert {(location.kind, location.title) for location in response.used_in} == {("chat", "Research"), ("project", "Launch")}


def test_legacy_filename_is_preserved_when_original_is_unknown():
    document = UserDocument(user_id=uuid.uuid4(), filename="Legacy.txt", status="ready", provider_artifacts=[])
    result = documents._document_to_response(document)
    assert result.filename == "Legacy.txt"
    assert result.original_filename is None
