import hashlib
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, HTTPException, UploadFile
from openai import AsyncOpenAI
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.database import engine
from app.db.models import AppUser, Conversation, ConversationDocument, UserDocument
from app.db.subscription_tiers import SubscriptionTier
from app.schemas.documents import (
    ConversationDocumentsUpdateResponse,
    DocumentCapabilitiesResponse,
    DocumentsListResponse,
    UserDocumentResponse,
)
from app.services.subscription_check.entitlements import get_active_tier

_openai_client = AsyncOpenAI()

DOCUMENT_STATUS_UPLOADING = "uploading"
DOCUMENT_STATUS_PROCESSING = "processing"
DOCUMENT_STATUS_READY = "ready"
DOCUMENT_STATUS_FAILED = "failed"
DOCUMENT_STATUS_DELETE_QUEUED = "delete_queued"
DOCUMENT_STATUS_DELETED = "deleted"
_ATTACHABLE_STATUSES = {
    DOCUMENT_STATUS_UPLOADING,
    DOCUMENT_STATUS_PROCESSING,
    DOCUMENT_STATUS_READY,
}
_PENDING_INDEXING_STATUSES = {
    DOCUMENT_STATUS_UPLOADING,
    DOCUMENT_STATUS_PROCESSING,
}

_OPENAI_FILE_LIMIT_BYTES = 512 * 1024 * 1024
_DEFAULT_EXTENSION_ALLOWLIST = {
    ".c",
    ".cpp",
    ".cs",
    ".css",
    ".doc",
    ".docx",
    ".go",
    ".html",
    ".java",
    ".js",
    ".json",
    ".md",
    ".pdf",
    ".php",
    ".pptx",
    ".py",
    ".rb",
    ".sh",
    ".tex",
    ".ts",
    ".txt",
}


@dataclass(frozen=True)
class _DocLimits:
    tier_name: Optional[str]
    max_active_docs: int
    max_storage_bytes: int
    max_file_size_bytes: int
    max_pinned_docs: int
    doc_retention_hours: int


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalize_tier_name(name: Optional[str]) -> str:
    return (name or "").strip().lower().replace("-", "_")


def _default_limits_for_tier_name(tier_name: Optional[str]) -> _DocLimits:
    normalized = _normalize_tier_name(tier_name)
    if "premium" in normalized:
        return _DocLimits(
            tier_name=tier_name,
            max_active_docs=200,
            max_storage_bytes=1024 * 1024 * 1024,
            max_file_size_bytes=512 * 1024 * 1024,
            max_pinned_docs=100,
            doc_retention_hours=24 * 5,
        )
    if "advanced" in normalized:
        return _DocLimits(
            tier_name=tier_name,
            max_active_docs=100,
            max_storage_bytes=500 * 1024 * 1024,
            max_file_size_bytes=250 * 1024 * 1024,
            max_pinned_docs=50,
            doc_retention_hours=24 * 5,
        )
    if "basic" in normalized:
        return _DocLimits(
            tier_name=tier_name,
            max_active_docs=50,
            max_storage_bytes=200 * 1024 * 1024,
            max_file_size_bytes=100 * 1024 * 1024,
            max_pinned_docs=25,
            doc_retention_hours=24 * 5,
        )
    return _DocLimits(
        tier_name=tier_name,
        max_active_docs=2,
        max_storage_bytes=10 * 1024 * 1024,
        max_file_size_bytes=5 * 1024 * 1024,
        max_pinned_docs=0,
        doc_retention_hours=24,
    )


def _tier_doc_limits(tier: SubscriptionTier | None) -> _DocLimits:
    defaults = _default_limits_for_tier_name(getattr(tier, "name", None))
    if tier is None:
        return defaults

    configured_storage = int(getattr(tier, "max_storage_bytes", 0) or 0)
    configured_docs = int(getattr(tier, "max_active_docs", 0) or 0)
    configured_pinned = int(getattr(tier, "max_pinned_docs", 0) or 0)
    configured_retention = int(getattr(tier, "doc_retention_hours", 0) or 0)
    configured_file = int(getattr(tier, "max_file_size_bytes", 0) or 0)

    storage = configured_storage if configured_storage > 0 else defaults.max_storage_bytes
    max_file = configured_file if configured_file > 0 else min(storage // 2, _OPENAI_FILE_LIMIT_BYTES)

    return _DocLimits(
        tier_name=tier.name,
        max_active_docs=configured_docs if configured_docs > 0 else defaults.max_active_docs,
        max_storage_bytes=storage,
        max_file_size_bytes=max_file,
        max_pinned_docs=max(0, configured_pinned if configured_pinned > 0 else defaults.max_pinned_docs),
        doc_retention_hours=configured_retention if configured_retention > 0 else defaults.doc_retention_hours,
    )


def _document_to_response(document: UserDocument) -> UserDocumentResponse:
    return UserDocumentResponse(
        id=document.id,
        filename=document.filename,
        mime_type=document.mime_type,
        size_bytes=int(document.size_bytes or 0),
        usage_bytes=int(document.usage_bytes or 0),
        status=document.status,  # type: ignore[arg-type]
        is_pinned=bool(document.is_pinned),
        last_used_in_search=document.last_used_in_search,
        expires_at=document.expires_at,
        created_at=document.created_at,
        updated_at=document.updated_at,
        error_code=document.error_code,
        error_message=document.error_message,
    )


def _active_documents_query(user_id: uuid.UUID):
    return (
        select(UserDocument)
        .where(
            UserDocument.user_id == user_id,
            UserDocument.deleted_at.is_(None),
            UserDocument.status != DOCUMENT_STATUS_DELETED,
        )
        .order_by(UserDocument.created_at.desc())
    )


async def get_document_capabilities(
    session: AsyncSession,
    user: AppUser,
) -> DocumentCapabilitiesResponse:
    tier = await get_active_tier(session, user.id)
    limits = _tier_doc_limits(tier)

    active_count = (
        await session.exec(
            select(func.count())
            .select_from(UserDocument)
            .where(
                UserDocument.user_id == user.id,
                UserDocument.deleted_at.is_(None),
                UserDocument.status != DOCUMENT_STATUS_DELETED,
            )
        )
    ).one() or 0

    pinned_count = (
        await session.exec(
            select(func.count())
            .select_from(UserDocument)
            .where(
                UserDocument.user_id == user.id,
                UserDocument.deleted_at.is_(None),
                UserDocument.status != DOCUMENT_STATUS_DELETED,
                UserDocument.is_pinned == True,
            )
        )
    ).one() or 0

    used_storage = (
        await session.exec(
            select(func.coalesce(func.sum(UserDocument.usage_bytes), 0))
            .where(
                UserDocument.user_id == user.id,
                UserDocument.deleted_at.is_(None),
                UserDocument.status != DOCUMENT_STATUS_DELETED,
            )
        )
    ).one() or 0

    return DocumentCapabilitiesResponse(
        status="active",
        tier_name=limits.tier_name,
        max_active_docs=limits.max_active_docs,
        active_doc_count=int(active_count),
        max_pinned_docs=limits.max_pinned_docs,
        pinned_doc_count=int(pinned_count),
        max_storage_bytes=limits.max_storage_bytes,
        used_storage_bytes=int(used_storage),
        remaining_storage_bytes=max(0, limits.max_storage_bytes - int(used_storage)),
        max_file_size_bytes=min(limits.max_file_size_bytes, _OPENAI_FILE_LIMIT_BYTES),
        doc_retention_hours=limits.doc_retention_hours,
    )


async def list_documents(session: AsyncSession, user: AppUser) -> DocumentsListResponse:
    documents = (await session.exec(_active_documents_query(user.id))).all()
    return DocumentsListResponse(documents=[_document_to_response(doc) for doc in documents])


async def _persist_upload_to_temp_file(upload: UploadFile) -> tuple[str, int, str]:
    suffix = Path(upload.filename or "upload").suffix
    fd, tmp_path = tempfile.mkstemp(prefix="doc-upload-", suffix=suffix)
    os.close(fd)

    total_bytes = 0
    hasher = hashlib.sha256()
    try:
        with open(tmp_path, "wb") as out:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
                hasher.update(chunk)
                out.write(chunk)
    finally:
        await upload.close()

    return tmp_path, total_bytes, hasher.hexdigest()


def _validate_extension(filename: str) -> None:
    ext = Path(filename or "").suffix.lower()
    if not ext or ext not in _DEFAULT_EXTENSION_ALLOWLIST:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "document_type_not_supported",
                "filename": filename,
            },
        )


def _refresh_expiration(document: UserDocument, retention_hours: int) -> None:
    if document.is_pinned:
        document.expires_at = None
        return
    document.expires_at = _utcnow_naive() + timedelta(hours=retention_hours)


async def upload_document(
    *,
    session: AsyncSession,
    user: AppUser,
    background_tasks: BackgroundTasks,
    upload: UploadFile,
) -> UserDocumentResponse:
    filename = upload.filename or "document"
    _validate_extension(filename)

    capabilities = await get_document_capabilities(session, user)
    if capabilities.active_doc_count >= capabilities.max_active_docs:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "documents_active_limit_reached",
                "max_active_docs": capabilities.max_active_docs,
            },
        )

    tmp_path, size_bytes, sha256 = await _persist_upload_to_temp_file(upload)
    if size_bytes <= 0:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise HTTPException(status_code=400, detail={"error": "empty_document"})

    if size_bytes > capabilities.max_file_size_bytes:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise HTTPException(
            status_code=409,
            detail={
                "error": "document_file_too_large",
                "size_bytes": size_bytes,
                "max_file_size_bytes": capabilities.max_file_size_bytes,
            },
        )

    if (capabilities.used_storage_bytes + size_bytes) > capabilities.max_storage_bytes:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise HTTPException(
            status_code=409,
            detail={
                "error": "document_storage_limit_reached",
                "used_storage_bytes": capabilities.used_storage_bytes,
                "max_storage_bytes": capabilities.max_storage_bytes,
            },
        )

    document = UserDocument(
        user_id=user.id,
        filename=filename,
        mime_type=upload.content_type,
        size_bytes=size_bytes,
        usage_bytes=size_bytes,
        sha256=sha256,
        status=DOCUMENT_STATUS_UPLOADING,
    )
    _refresh_expiration(document, capabilities.doc_retention_hours)
    session.add(document)
    await session.commit()
    await session.refresh(document)

    background_tasks.add_task(_ingest_document_background, document.id, tmp_path)
    return _document_to_response(document)


async def _ingest_document_background(document_id: uuid.UUID, tmp_path: str) -> None:
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            document = await session.get(UserDocument, document_id)
            if not document:
                return

            document.status = DOCUMENT_STATUS_PROCESSING
            document.error_code = None
            document.error_message = None
            session.add(document)
            await session.commit()

            vector_store = await _openai_client.vector_stores.create(
                name=f"user-document-{document.id}",
            )

            vector_file = await _openai_client.vector_stores.files.upload_and_poll(
                vector_store_id=vector_store.id,
                file=Path(tmp_path),
            )

            document.openai_vector_store_id = vector_store.id
            document.openai_file_id = getattr(vector_file, "file_id", None)
            document.status = DOCUMENT_STATUS_READY
            session.add(document)
            await session.commit()
    except Exception as exc:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            document = await session.get(UserDocument, document_id)
            if document:
                document.status = DOCUMENT_STATUS_FAILED
                document.error_code = "openai_ingest_failed"
                document.error_message = str(exc)[:1000]
                session.add(document)
                await session.commit()
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


async def delete_document(
    *,
    session: AsyncSession,
    user: AppUser,
    document_id: uuid.UUID,
    background_tasks: BackgroundTasks,
) -> None:
    document = await session.get(UserDocument, document_id)
    if not document or document.user_id != user.id or document.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Document not found")

    document.status = DOCUMENT_STATUS_DELETE_QUEUED
    session.add(document)

    links = (await session.exec(
        select(ConversationDocument).where(ConversationDocument.document_id == document.id)
    )).all()
    for link in links:
        await session.delete(link)

    await session.commit()
    background_tasks.add_task(_delete_document_background, document.id)


async def _delete_document_background(document_id: uuid.UUID) -> None:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        document = await session.get(UserDocument, document_id)
        if not document or document.deleted_at is not None:
            return

        try:
            if document.openai_vector_store_id:
                await _openai_client.vector_stores.delete(vector_store_id=document.openai_vector_store_id)
            if document.openai_file_id:
                await _openai_client.files.delete(file_id=document.openai_file_id)
        except Exception as exc:
            document.status = DOCUMENT_STATUS_FAILED
            document.error_code = "openai_delete_failed"
            document.error_message = str(exc)[:1000]
            session.add(document)
            await session.commit()
            return

        document.status = DOCUMENT_STATUS_DELETED
        document.deleted_at = _utcnow_naive()
        document.openai_file_id = None
        document.openai_vector_store_id = None
        session.add(document)
        await session.commit()


async def set_document_pin_state(
    *,
    session: AsyncSession,
    user: AppUser,
    document_id: uuid.UUID,
    pin: bool,
) -> UserDocumentResponse:
    document = await session.get(UserDocument, document_id)
    if not document or document.user_id != user.id or document.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Document not found")

    capabilities = await get_document_capabilities(session, user)
    if pin and capabilities.max_pinned_docs <= 0:
        raise HTTPException(status_code=403, detail={"error": "document_pinning_not_allowed"})
    if pin and (not document.is_pinned) and capabilities.pinned_doc_count >= capabilities.max_pinned_docs:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "documents_pinned_limit_reached",
                "max_pinned_docs": capabilities.max_pinned_docs,
            },
        )

    document.is_pinned = pin
    _refresh_expiration(document, capabilities.doc_retention_hours)
    session.add(document)
    await session.commit()
    await session.refresh(document)
    return _document_to_response(document)


async def replace_conversation_documents(
    *,
    session: AsyncSession,
    user: AppUser,
    conversation_id: uuid.UUID,
    document_ids: list[uuid.UUID],
) -> ConversationDocumentsUpdateResponse:
    conversation = await session.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    normalized_ids = list(dict.fromkeys(document_ids))
    caps: DocumentCapabilitiesResponse | None = None
    docs_to_refresh: list[UserDocument] = []

    if normalized_ids:
        docs = (await session.exec(
            select(UserDocument).where(
                UserDocument.id.in_(normalized_ids),
                UserDocument.user_id == user.id,
                UserDocument.deleted_at.is_(None),
                UserDocument.status.in_(tuple(_ATTACHABLE_STATUSES)),
            )
        )).all()
        found_ids = {doc.id for doc in docs}
        missing = [doc_id for doc_id in normalized_ids if doc_id not in found_ids]
        if missing:
            raise HTTPException(
                status_code=400,
                detail={"error": "documents_not_ready_or_not_owned", "document_ids": [str(x) for x in missing]},
            )
        caps = await get_document_capabilities(session, user)
        docs_to_refresh = (await session.exec(
            select(UserDocument).where(UserDocument.id.in_(normalized_ids))
        )).all()

    existing_links = (await session.exec(
        select(ConversationDocument).where(ConversationDocument.conversation_id == conversation_id)
    )).all()
    for link in existing_links:
        await session.delete(link)
    # Flush deletions before inserting new rows so `(conversation_id, document_id)`
    # unique constraint does not trip when users keep previously attached docs selected.
    await session.flush()

    now = _utcnow_naive()
    for doc_id in normalized_ids:
        session.add(ConversationDocument(conversation_id=conversation_id, document_id=doc_id, attached_at=now))

    if normalized_ids and caps is not None:
        for doc in docs_to_refresh:
            _refresh_expiration(doc, caps.doc_retention_hours)
            session.add(doc)

    await session.commit()
    return ConversationDocumentsUpdateResponse(conversation_id=conversation_id, document_ids=normalized_ids)


async def list_conversation_document_ids(
    *,
    session: AsyncSession,
    user: AppUser,
    conversation_id: uuid.UUID,
) -> ConversationDocumentsUpdateResponse:
    conversation = await session.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    links = (await session.exec(
        select(ConversationDocument.document_id)
        .join(UserDocument, UserDocument.id == ConversationDocument.document_id)
        .where(
            ConversationDocument.conversation_id == conversation_id,
            UserDocument.deleted_at.is_(None),
        )
    )).all()

    unique_ids: list[uuid.UUID] = []
    for doc_id in links:
        if doc_id and doc_id not in unique_ids:
            unique_ids.append(doc_id)

    return ConversationDocumentsUpdateResponse(
        conversation_id=conversation_id,
        document_ids=unique_ids,
    )


async def list_conversation_ready_vector_store_ids(
    session: AsyncSession,
    conversation_id: uuid.UUID,
) -> list[str]:
    rows = (await session.exec(
        select(UserDocument.openai_vector_store_id)
        .join(ConversationDocument, ConversationDocument.document_id == UserDocument.id)
        .where(
            ConversationDocument.conversation_id == conversation_id,
            UserDocument.deleted_at.is_(None),
            UserDocument.status == DOCUMENT_STATUS_READY,
            UserDocument.openai_vector_store_id.is_not(None),
        )
    )).all()

    out: list[str] = []
    for row in rows:
        if row and row not in out:
            out.append(row)
    return out


async def count_conversation_pending_indexing_documents(
    session: AsyncSession,
    conversation_id: uuid.UUID,
) -> int:
    count = (
        await session.exec(
            select(func.count())
            .select_from(ConversationDocument)
            .join(UserDocument, UserDocument.id == ConversationDocument.document_id)
            .where(
                ConversationDocument.conversation_id == conversation_id,
                UserDocument.deleted_at.is_(None),
                UserDocument.status.in_(tuple(_PENDING_INDEXING_STATUSES)),
            )
        )
    ).one()
    return int(count or 0)


async def touch_conversation_documents_last_used_in_search(
    session: AsyncSession,
    conversation_id: uuid.UUID,
) -> None:
    links = (await session.exec(
        select(ConversationDocument).where(ConversationDocument.conversation_id == conversation_id)
    )).all()
    if not links:
        return

    now = _utcnow_naive()
    doc_ids = [link.document_id for link in links]
    docs = (await session.exec(
        select(UserDocument).where(UserDocument.id.in_(doc_ids), UserDocument.deleted_at.is_(None))
    )).all()

    for document in docs:
        document.last_used_in_search = now
        session.add(document)
    await session.commit()
