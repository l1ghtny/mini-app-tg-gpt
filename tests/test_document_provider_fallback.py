import io
import os
from datetime import datetime, timezone

import pytest
from fastapi import BackgroundTasks, UploadFile
from starlette.datastructures import Headers
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

import app.api.document_helpers as document_helpers
from app.schemas.documents import DocumentCapabilitiesResponse
from app.db.models import AppUser, Conversation, ConversationDocument, DocumentProviderArtifact, UserDocument


@pytest.mark.asyncio
async def test_document_provider_fallback_keeps_openai_retrieval_working(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)
    monkeypatch.setattr(document_helpers.settings, "GOOGLE_DOCUMENTS_ENABLED", False, raising=False)
    monkeypatch.setattr(document_helpers.settings, "DOCUMENT_PROVIDER_FALLBACK_ENABLED", True, raising=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=721000901, default_document_provider="google")
        conversation = Conversation(title="Docs", user_id=user.id)
        session.add(user)
        await session.flush()
        session.add(conversation)
        await session.flush()

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        document = UserDocument(
            user_id=user.id,
            filename="notes.txt",
            mime_type="text/plain",
            size_bytes=10,
            usage_bytes=10,
            status="ready",
            openai_file_id="file-openai-1",
            openai_vector_store_id="vs-openai-1",
            created_at=now,
            updated_at=now,
        )
        session.add(document)
        await session.flush()
        session.add(
            DocumentProviderArtifact(
                document_id=document.id,
                provider="openai",
                status="ready",
                external_file_id="file-openai-1",
                external_index_id="vs-openai-1",
                indexed_at=now,
            )
        )
        await session.flush()
        session.add(ConversationDocument(conversation_id=conversation.id, document_id=document.id, attached_at=now))
        await session.commit()
        await session.refresh(user)
        await session.refresh(conversation)

        attach = await document_helpers.replace_conversation_documents(
            session=session,
            user=user,
            conversation_id=conversation.id,
            document_ids=[document.id],
            provider_override="google",
        )
        vector_store_ids = await document_helpers.list_conversation_ready_vector_store_ids(
            session,
            conversation.id,
            user=user,
            provider_override="google",
        )
        listing = await document_helpers.list_documents(session, user)

    await engine.dispose()

    assert attach.effective_provider == "openai"
    assert vector_store_ids == ["vs-openai-1"]
    assert listing.documents[0].primary_provider == "openai"
    assert listing.documents[0].provider_artifacts[0].provider == "openai"


@pytest.mark.asyncio
async def test_legacy_openai_document_without_artifact_still_resolves_vector_store(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)
    monkeypatch.setattr(document_helpers.settings, "DOCUMENT_PROVIDER_FALLBACK_ENABLED", True, raising=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=721000902)
        session.add(user)
        await session.flush()

        conversation = Conversation(title="Legacy docs", user_id=user.id)
        session.add(conversation)
        await session.flush()

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        document = UserDocument(
            user_id=user.id,
            filename="legacy.txt",
            mime_type="text/plain",
            size_bytes=10,
            usage_bytes=10,
            status="ready",
            openai_vector_store_id="vs-legacy-1",
            created_at=now,
            updated_at=now,
        )
        session.add(document)
        await session.flush()
        session.add(ConversationDocument(conversation_id=conversation.id, document_id=document.id, attached_at=now))
        await session.commit()
        await session.refresh(user)
        await session.refresh(conversation)

        vector_store_ids = await document_helpers.list_conversation_ready_vector_store_ids(
            session,
            conversation.id,
            user=user,
        )

    await engine.dispose()

    assert vector_store_ids == ["vs-legacy-1"]


@pytest.mark.asyncio
async def test_upload_document_returns_response_without_lazy_loading_provider_artifacts(monkeypatch, tmp_path):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async def fake_capabilities(session, user):
        return DocumentCapabilitiesResponse(
            status="active",
            tier_name=None,
            max_active_docs=2,
            active_doc_count=0,
            max_pinned_docs=0,
            pinned_doc_count=0,
            max_storage_bytes=10 * 1024 * 1024,
            used_storage_bytes=0,
            remaining_storage_bytes=10 * 1024 * 1024,
            max_file_size_bytes=5 * 1024 * 1024,
            doc_retention_hours=24,
        )

    persisted_file = tmp_path / "notes.txt"
    persisted_file.write_bytes(b"hello")

    async def fake_persist_upload_to_temp_file(upload, target_filename):
        return str(persisted_file), 5, "sha256-1"

    monkeypatch.setattr(document_helpers, "get_document_capabilities", fake_capabilities)
    monkeypatch.setattr(document_helpers, "_persist_upload_to_temp_file", fake_persist_upload_to_temp_file)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=721000903)
        session.add(user)
        await session.commit()
        await session.refresh(user)

        upload = UploadFile(
            filename="notes.txt",
            file=io.BytesIO(b"hello"),
            headers=Headers({"content-type": "text/plain"}),
        )
        background_tasks = BackgroundTasks()

        response = await document_helpers.upload_document(
            session=session,
            user=user,
            background_tasks=background_tasks,
            upload=upload,
        )

    await engine.dispose()

    assert response.filename.endswith(".txt")
    assert response.status == "uploading"
    assert response.primary_provider == "openai"
    assert len(response.provider_artifacts) == 1
    assert response.provider_artifacts[0].provider == "openai"
    assert response.provider_artifacts[0].status == "uploading"
    assert len(background_tasks.tasks) == 1
