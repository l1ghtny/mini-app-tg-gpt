import asyncio
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from openai import NotFoundError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from app.api import document_helpers as documents
from app.core.config import settings
from app.db.database import engine
from app.db.models import AppUser, ChatFolder, ChatFolderDocument, Conversation, ConversationDocument, DocumentProviderArtifact, UserDocument
from jobs.migrate_document_retention import migrate_batch
from jobs.cleanup_documents import main as cleanup


@pytest.mark.asyncio
async def test_migration_grants_owner_retention_once_and_preserves_pinned_and_future_files(monkeypatch):
    now = documents._utcnow_naive()
    limits = AsyncMock(return_value=SimpleNamespace(doc_retention_hours=120))
    monkeypatch.setattr("jobs.migrate_document_retention._document_limits_for_user", limits)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=321001)
        session.add(user); await session.flush()
        expired = UserDocument(user_id=user.id, filename="Old.txt", status="ready", expires_at=now-timedelta(days=20))
        pinned = UserDocument(user_id=user.id, filename="Keep.txt", status="ready", is_pinned=True)
        future = UserDocument(user_id=user.id, filename="Future.txt", status="ready", expires_at=now+timedelta(days=3))
        session.add_all([expired, pinned, future]); await session.commit()
        assert await migrate_batch(session) == 3
        await session.refresh(expired); await session.refresh(pinned); await session.refresh(future)
        expiry = expired.expires_at
        assert now+timedelta(hours=120) <= expiry <= documents._utcnow_naive()+timedelta(hours=120)
        assert pinned.is_pinned and pinned.expires_at is None
        assert future.expires_at == now+timedelta(days=3)
        assert await migrate_batch(session) == 0
        await session.refresh(expired)
        assert expired.expires_at == expiry


@pytest.mark.asyncio
async def test_expired_files_cannot_be_attached_searched_or_pinned_but_pinned_files_remain_searchable(monkeypatch):
    monkeypatch.setattr(settings, "DOCUMENT_RETENTION_ENFORCED", True)
    now = documents._utcnow_naive()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=321002); session.add(user); await session.flush()
        conversation = Conversation(user_id=user.id)
        expired = UserDocument(user_id=user.id, filename="Expired.txt", status="ready", expires_at=now-timedelta(seconds=1), retention_migrated_at=now, provider_artifacts=[])
        pinned = UserDocument(user_id=user.id, filename="Pinned.txt", status="ready", is_pinned=True, expires_at=now-timedelta(days=2), retention_migrated_at=now, provider_artifacts=[])
        unmigrated = UserDocument(user_id=user.id, filename="Legacy.txt", status="ready", expires_at=now-timedelta(days=20), provider_artifacts=[])
        session.add_all([conversation, expired, pinned, unmigrated]); await session.flush()
        for file, store in [(expired, "vs-expired"), (pinned, "vs-pinned"), (unmigrated, "vs-legacy")]:
            file.provider_artifacts.append(DocumentProviderArtifact(document_id=file.id, status="ready", external_index_id=store))
            session.add(file); session.add(ConversationDocument(conversation_id=conversation.id, document_id=file.id))
        await session.commit()
        assert not documents._document_has_attachable_state(expired, "openai")
        assert documents._document_to_response(expired).retention_state == "expired"
        assert documents._document_to_response(unmigrated).retention_state == "overdue"
        assert set(await documents.list_conversation_ready_vector_store_ids(session, conversation.id, user=user)) == {"vs-pinned", "vs-legacy"}
        with pytest.raises(HTTPException) as error:
            await documents.set_document_pin_state(session=session, user=user, document_id=expired.id, pin=True)
        assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_successful_search_renews_only_captured_stores(monkeypatch):
    monkeypatch.setattr(settings, "DOCUMENT_RETENTION_ENFORCED", True)
    monkeypatch.setattr(documents, "_document_limits_for_user", AsyncMock(return_value=SimpleNamespace(doc_retention_hours=120)))
    now = documents._utcnow_naive()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=321003); session.add(user); await session.flush()
        conversation = Conversation(user_id=user.id)
        file = UserDocument(user_id=user.id, filename="Active.txt", status="ready", openai_vector_store_id="vs-active", expires_at=now+timedelta(hours=1), retention_migrated_at=now)
        expired = UserDocument(user_id=user.id, filename="Expired.txt", status="ready", expires_at=now-timedelta(hours=1), retention_migrated_at=now)
        session.add_all([conversation, file, expired]); await session.flush()
        session.add_all([ConversationDocument(conversation_id=conversation.id, document_id=doc.id) for doc in [file, expired]])
        await session.commit()
        await documents.touch_documents_last_used_in_search(session, user_id=user.id, vector_store_ids=["vs-active"])
        await session.refresh(file); await session.refresh(expired)
        assert file.expires_at >= now+timedelta(hours=120)
        assert file.last_used_in_search is not None
        assert expired.last_used_in_search is None


@pytest.mark.asyncio
async def test_cleanup_detaches_all_context_and_retries_provider_failure_without_resurrection(monkeypatch):
    monkeypatch.setattr(settings, "DOCUMENT_RETENTION_ENFORCED", True)
    now = documents._utcnow_naive()
    not_found = NotFoundError("already deleted", response=httpx.Response(404, request=httpx.Request("DELETE", "https://api.openai.com/test")), body={})
    delete_index = AsyncMock(side_effect=[None, not_found])
    delete_file = AsyncMock(side_effect=[RuntimeError("temporary error"), None])
    monkeypatch.setattr(documents, "_openai_client", SimpleNamespace(vector_stores=SimpleNamespace(delete=delete_index), files=SimpleNamespace(delete=delete_file)))
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=321004); session.add(user); await session.flush()
        conversation = Conversation(user_id=user.id); project = ChatFolder(user_id=user.id, name="Launch")
        file = UserDocument(user_id=user.id, filename="Expired.txt", status="ready", expires_at=now-timedelta(hours=2), retention_migrated_at=now, provider_artifacts=[])
        pinned = UserDocument(user_id=user.id, filename="Pinned.txt", status="ready", is_pinned=True, expires_at=now-timedelta(days=1), retention_migrated_at=now)
        legacy = UserDocument(user_id=user.id, filename="Legacy.txt", status="ready", expires_at=now-timedelta(days=1))
        session.add_all([conversation, project, file, pinned, legacy]); await session.flush()
        file.provider_artifacts.append(DocumentProviderArtifact(document_id=file.id, status="ready", external_index_id="vs-delete", external_file_id="file-delete")); session.add(file)
        session.add_all([ConversationDocument(conversation_id=conversation.id, document_id=file.id), ChatFolderDocument(folder_id=project.id, document_id=file.id)])
        await session.commit()
        await cleanup()
        await session.refresh(file)
        assert file.status == "delete_queued" and file.deleted_at is None
        assert not documents._document_has_attachable_state(file, "openai")
        assert (await session.exec(select(ConversationDocument))).all() == []
        assert (await session.exec(select(ChatFolderDocument))).all() == []
        assert (await documents.get_document_capabilities(session, user)).active_doc_count == 3
        await cleanup()
        await session.refresh(file); await session.refresh(pinned); await session.refresh(legacy)
        assert file.status == "deleted" and file.deleted_at is not None
        assert pinned.status == "ready" and pinned.deleted_at is None
        assert legacy.status == "ready" and legacy.deleted_at is None
        assert legacy.retention_migrated_at is not None
        assert legacy.expires_at > now
        assert (await documents.get_document_capabilities(session, user)).active_doc_count == 2


@pytest.mark.asyncio
async def test_ingestion_cannot_overwrite_a_concurrent_deletion(monkeypatch, tmp_path):
    upload = AsyncMock()
    monkeypatch.setattr(documents, "_ingest_openai_artifact", upload)
    file_path = tmp_path / "upload.txt"
    file_path.write_text("fixture")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=321005)
        session.add(user)
        await session.flush()
        file = UserDocument(user_id=user.id, filename="Race.txt", status="uploading", provider_artifacts=[])
        session.add(file)
        await session.commit()
        await session.exec(select(UserDocument).where(UserDocument.id == file.id).with_for_update())
        file.status = "delete_queued"
        session.add(file)
        await session.flush()
        ingestion = asyncio.create_task(documents._ingest_document_background(file.id, str(file_path), ["openai"]))
        try:
            # The worker must wait for deletion's row lock, then read its result.
            await asyncio.sleep(0.1)
            assert not ingestion.done()
            await session.commit()
            await asyncio.wait_for(ingestion, timeout=5)
        finally:
            if not ingestion.done():
                ingestion.cancel()
                await asyncio.gather(ingestion, return_exceptions=True)
        await session.refresh(file)
        assert file.status == "delete_queued"
        upload.assert_not_awaited()
        assert not file_path.exists()


@pytest.mark.asyncio
async def test_upload_response_read_does_not_block_its_background_worker(monkeypatch, tmp_path):
    upload = AsyncMock()
    monkeypatch.setattr(documents, "_ingest_openai_artifact", upload)
    file_path = tmp_path / "response.txt"
    file_path.write_text("fixture")
    async with AsyncSession(engine, expire_on_commit=False) as request_session:
        user = AppUser(telegram_id=321006)
        request_session.add(user)
        await request_session.flush()
        file = UserDocument(user_id=user.id, filename="Response.txt", status="uploading", provider_artifacts=[])
        request_session.add(file)
        await request_session.commit()
        # FastAPI keeps this request dependency open until BackgroundTasks finish.
        response_file = await documents._load_document_for_user(request_session, user_id=user.id, document_id=file.id)
        assert response_file is not None
        await asyncio.wait_for(documents._ingest_document_background(file.id, str(file_path), ["openai"]), timeout=5)
        upload.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_manual_deletions_rotate_without_retention_enforcement(monkeypatch):
    monkeypatch.setattr(settings, "DOCUMENT_RETENTION_ENFORCED", False)
    delete_file = AsyncMock(side_effect=RuntimeError("supplier unavailable"))
    monkeypatch.setattr(documents, "_delete_provider_file", delete_file)
    now = documents._utcnow_naive()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=321007)
        session.add(user)
        await session.flush()
        older = UserDocument(user_id=user.id, filename="Older.txt", status="delete_queued", updated_at=now-timedelta(days=2), provider_artifacts=[])
        newer = UserDocument(user_id=user.id, filename="Newer.txt", status="delete_queued", updated_at=now-timedelta(days=1), provider_artifacts=[])
        expired = UserDocument(user_id=user.id, filename="Expired.txt", status="ready", expires_at=now-timedelta(days=3), retention_migrated_at=now)
        session.add_all([older, newer, expired])
        await session.flush()
        for file in [older, newer]:
            file.provider_artifacts.append(DocumentProviderArtifact(document_id=file.id, status="delete_queued", external_file_id=str(file.id)))
            session.add(file)
        await session.commit()
        await cleanup(batch_size=1)
        await session.refresh(older)
        first_retry = older.updated_at
        assert first_retry >= now
        await cleanup(batch_size=1)
        assert delete_file.await_args_list[0].args == (str(older.id),)
        assert delete_file.await_args_list[1].args == (str(newer.id),)
        await cleanup(batch_size=1)
        await session.refresh(older)
        assert older.updated_at > first_retry
        await session.refresh(expired)
        assert expired.status == "ready" and expired.deleted_at is None


@pytest.mark.asyncio
async def test_search_renews_its_captured_files_after_detachment_and_mid_request_expiry(monkeypatch):
    monkeypatch.setattr(settings, "DOCUMENT_RETENTION_ENFORCED", True)
    monkeypatch.setattr(documents, "_document_limits_for_user", AsyncMock(return_value=SimpleNamespace(doc_retention_hours=120)))
    admitted_at = documents._utcnow_naive()
    finished_at = admitted_at + timedelta(seconds=45)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=321008)
        other = AppUser(telegram_id=321009)
        session.add_all([user, other])
        await session.flush()
        conversation = Conversation(user_id=user.id)
        searched = UserDocument(user_id=user.id, filename="Searched.txt", status="ready", openai_vector_store_id="vs-searched", expires_at=admitted_at+timedelta(seconds=30), retention_migrated_at=admitted_at)
        replacement = UserDocument(user_id=user.id, filename="Replacement.txt", status="ready", openai_vector_store_id="vs-replacement", expires_at=admitted_at+timedelta(hours=1), retention_migrated_at=admitted_at)
        foreign = UserDocument(user_id=other.id, filename="Foreign.txt", status="ready", openai_vector_store_id="vs-foreign", expires_at=admitted_at+timedelta(hours=1), retention_migrated_at=admitted_at)
        queued = UserDocument(user_id=user.id, filename="Deleted.txt", status="delete_queued", openai_vector_store_id="vs-deleted", expires_at=admitted_at+timedelta(hours=1), retention_migrated_at=admitted_at)
        session.add_all([conversation, searched, replacement, foreign, queued])
        await session.flush()
        # Selection has changed after admission: A is detached and B attached.
        session.add(ConversationDocument(conversation_id=conversation.id, document_id=replacement.id))
        await session.commit()
        replacement_expiry = replacement.expires_at
        monkeypatch.setattr(documents, "_utcnow_naive", lambda: finished_at)
        await documents.touch_documents_last_used_in_search(session, user_id=user.id, vector_store_ids=["vs-searched", "vs-foreign", "vs-deleted"])
        for file in [searched, replacement, foreign, queued]:
            await session.refresh(file)
        assert searched.last_used_in_search == finished_at
        assert searched.expires_at == finished_at+timedelta(hours=120)
        assert replacement.last_used_in_search is None and replacement.expires_at == replacement_expiry
        assert foreign.last_used_in_search is None and queued.last_used_in_search is None
