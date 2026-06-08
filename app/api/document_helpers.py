import hashlib
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, HTTPException, UploadFile
from openai import AsyncOpenAI
from sqlalchemy.orm import selectinload
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.metrics import track_event
from app.db.database import engine
from app.db.models import (
    AppUser,
    Conversation,
    ConversationDocument,
    DocumentProvider,
    DocumentProviderArtifact,
    DocumentProviderArtifactStatus,
    UserDocument,
)
from app.db.subscription_tiers import SubscriptionTier
from app.schemas.documents import (
    ConversationDocumentsUpdateResponse,
    DocumentCapabilitiesResponse,
    DocumentProviderArtifactResponse,
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
DOCUMENT_PROVIDER_OPENAI = DocumentProvider.openai.value
DOCUMENT_PROVIDER_GOOGLE = DocumentProvider.google.value
_ATTACHABLE_STATUSES = {
    DOCUMENT_STATUS_UPLOADING,
    DOCUMENT_STATUS_PROCESSING,
    DOCUMENT_STATUS_READY,
}
_PENDING_INDEXING_STATUSES = {
    DOCUMENT_STATUS_UPLOADING,
    DOCUMENT_STATUS_PROCESSING,
}
_ACTIVE_ARTIFACT_STATUSES = {
    DocumentProviderArtifactStatus.uploading.value,
    DocumentProviderArtifactStatus.processing.value,
    DocumentProviderArtifactStatus.ready.value,
    DocumentProviderArtifactStatus.failed.value,
    DocumentProviderArtifactStatus.delete_queued.value,
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
_MAX_DISPLAY_FILENAME_LENGTH = 180
_SAFE_FILENAME_CHARS_PATTERN = re.compile(r"[^\w.\- ]+", flags=re.UNICODE)


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


def _normalize_document_provider(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    if not value:
        value = (settings.DOCUMENT_PROVIDER_DEFAULT or DOCUMENT_PROVIDER_OPENAI).strip().lower()
    if value not in {DOCUMENT_PROVIDER_OPENAI, DOCUMENT_PROVIDER_GOOGLE}:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_document_provider", "provider": raw},
        )
    return value


def _provider_is_enabled(provider: str) -> bool:
    if provider == DOCUMENT_PROVIDER_OPENAI:
        return True
    if provider == DOCUMENT_PROVIDER_GOOGLE:
        return bool(settings.GOOGLE_DOCUMENTS_ENABLED)
    return False


def _resolve_document_provider(
    *,
    user: AppUser | None,
    provider_override: str | None = None,
    strict: bool = True,
) -> tuple[str, str, bool]:
    requested = _normalize_document_provider(
        provider_override
        or getattr(user, "default_document_provider", None)
        or settings.DOCUMENT_PROVIDER_DEFAULT
        or DOCUMENT_PROVIDER_OPENAI
    )
    if _provider_is_enabled(requested):
        return requested, requested, False
    if settings.DOCUMENT_PROVIDER_FALLBACK_ENABLED:
        return DOCUMENT_PROVIDER_OPENAI, requested, True
    if strict:
        raise HTTPException(
            status_code=409,
            detail={"error": "document_provider_unavailable", "provider": requested},
        )
    return requested, requested, False


def _track_provider_fallback(user: AppUser | None, requested_provider: str, effective_provider: str) -> None:
    if not user or requested_provider == effective_provider:
        return
    track_event(
        "documents.provider_fallback",
        str(user.id),
        {
            "requested_provider": requested_provider,
            "effective_provider": effective_provider,
        },
    )


def _artifact_sort_key(artifact: DocumentProviderArtifact) -> tuple[int, datetime]:
    status_order = {
        DocumentProviderArtifactStatus.ready.value: 0,
        DocumentProviderArtifactStatus.processing.value: 1,
        DocumentProviderArtifactStatus.uploading.value: 2,
        DocumentProviderArtifactStatus.failed.value: 3,
        DocumentProviderArtifactStatus.delete_queued.value: 4,
        DocumentProviderArtifactStatus.deleted.value: 5,
    }
    return (
        status_order.get(artifact.status, 9),
        artifact.created_at or _utcnow_naive(),
    )


def _legacy_openai_artifact(document: UserDocument) -> DocumentProviderArtifact | None:
    if not (
        document.openai_file_id
        or document.openai_vector_store_id
        or document.status in _ATTACHABLE_STATUSES
        or document.status == DOCUMENT_STATUS_FAILED
    ):
        return None
    return DocumentProviderArtifact(
        document_id=document.id,
        provider=DOCUMENT_PROVIDER_OPENAI,
        status=document.status,
        external_file_id=document.openai_file_id,
        external_index_id=document.openai_vector_store_id,
        error_code=document.error_code,
        error_message=document.error_message,
        indexed_at=document.updated_at if document.status == DOCUMENT_STATUS_READY else None,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def _active_provider_artifacts(document: UserDocument) -> list[DocumentProviderArtifact]:
    artifacts = sorted(
        [
            artifact
            for artifact in (document.provider_artifacts or [])
            if artifact.deleted_at is None and artifact.status in _ACTIVE_ARTIFACT_STATUSES
        ],
        key=_artifact_sort_key,
    )
    if artifacts:
        return artifacts
    legacy = _legacy_openai_artifact(document)
    return [legacy] if legacy else []


def _artifact_response(artifact: DocumentProviderArtifact) -> DocumentProviderArtifactResponse:
    return DocumentProviderArtifactResponse(
        provider=artifact.provider,  # type: ignore[arg-type]
        status=artifact.status,  # type: ignore[arg-type]
        external_file_id=artifact.external_file_id,
        external_index_id=artifact.external_index_id,
        error_code=artifact.error_code,
        error_message=artifact.error_message,
        indexed_at=artifact.indexed_at,
    )


def _sync_document_from_artifacts(document: UserDocument) -> None:
    artifacts = _active_provider_artifacts(document)
    if not artifacts:
        return

    openai_artifact = next(
        (artifact for artifact in artifacts if artifact.provider == DOCUMENT_PROVIDER_OPENAI),
        None,
    )
    if openai_artifact is not None:
        document.openai_file_id = openai_artifact.external_file_id
        document.openai_vector_store_id = openai_artifact.external_index_id
    else:
        document.openai_file_id = None
        document.openai_vector_store_id = None

    if any(artifact.status == DocumentProviderArtifactStatus.ready.value for artifact in artifacts):
        document.status = DOCUMENT_STATUS_READY
        document.error_code = None
        document.error_message = None
        return

    if any(artifact.status == DocumentProviderArtifactStatus.processing.value for artifact in artifacts):
        document.status = DOCUMENT_STATUS_PROCESSING
        document.error_code = None
        document.error_message = None
        return

    if any(artifact.status == DocumentProviderArtifactStatus.uploading.value for artifact in artifacts):
        document.status = DOCUMENT_STATUS_UPLOADING
        document.error_code = None
        document.error_message = None
        return

    if any(artifact.status == DocumentProviderArtifactStatus.delete_queued.value for artifact in artifacts):
        document.status = DOCUMENT_STATUS_DELETE_QUEUED
        return

    failed_artifact = next(
        (artifact for artifact in artifacts if artifact.status == DocumentProviderArtifactStatus.failed.value),
        None,
    )
    if failed_artifact is not None:
        document.status = DOCUMENT_STATUS_FAILED
        document.error_code = failed_artifact.error_code
        document.error_message = failed_artifact.error_message


def _document_primary_provider(document: UserDocument, preferred_provider: str | None = None) -> str:
    artifacts = _active_provider_artifacts(document)
    if preferred_provider and any(artifact.provider == preferred_provider for artifact in artifacts):
        return preferred_provider
    if artifacts:
        return artifacts[0].provider
    return DOCUMENT_PROVIDER_OPENAI


def _document_to_response(
    document: UserDocument,
    *,
    preferred_provider: str | None = None,
) -> UserDocumentResponse:
    _sync_document_from_artifacts(document)
    artifacts = _active_provider_artifacts(document)
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
        primary_provider=_document_primary_provider(document, preferred_provider),  # type: ignore[arg-type]
        provider_artifacts=[_artifact_response(artifact) for artifact in artifacts],
    )


def _active_documents_query(user_id: uuid.UUID):
    return (
        select(UserDocument)
        .where(
            UserDocument.user_id == user_id,
            UserDocument.deleted_at.is_(None),
            UserDocument.status != DOCUMENT_STATUS_DELETED,
        )
        .options(selectinload(UserDocument.provider_artifacts))
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
    preferred_provider, _, _ = _resolve_document_provider(user=user, strict=False)
    documents = (await session.exec(_active_documents_query(user.id))).all()
    return DocumentsListResponse(
        documents=[
            _document_to_response(doc, preferred_provider=preferred_provider)
            for doc in documents
        ]
    )


def _cleanup_temp_path(tmp_path: str) -> None:
    try:
        os.remove(tmp_path)
    except OSError:
        pass
    try:
        parent = os.path.dirname(tmp_path)
        if parent:
            os.rmdir(parent)
    except OSError:
        pass


async def _persist_upload_to_temp_file(upload: UploadFile, target_filename: str) -> tuple[str, int, str]:
    safe_name = Path(target_filename or "upload").name or "upload"
    tmp_dir = tempfile.mkdtemp(prefix="doc-upload-")
    tmp_path = os.path.join(tmp_dir, safe_name)

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


def _safe_filename_part(value: str, fallback: str) -> str:
    cleaned = _SAFE_FILENAME_CHARS_PATTERN.sub("_", (value or "").strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("._- ")
    return cleaned or fallback


def _build_display_filename(*, original_filename: str, user: AppUser, uploaded_at: datetime) -> str:
    original_basename = Path(original_filename).name or "document"
    ext = Path(original_basename).suffix.lower()
    original_stem = Path(original_basename).stem

    safe_stem = _safe_filename_part(original_stem, "document")
    user_label = _safe_filename_part(user.telegram_username or str(user.telegram_id), "user")
    timestamp = uploaded_at.strftime("%Y%m%d-%H%M%S")

    suffix = f"-{user_label}-{timestamp}{ext}"
    max_stem_len = max(1, _MAX_DISPLAY_FILENAME_LENGTH - len(suffix))
    if len(safe_stem) > max_stem_len:
        safe_stem = safe_stem[:max_stem_len].rstrip("._- ") or "document"

    return f"{safe_stem}{suffix}"


def _refresh_expiration(document: UserDocument, retention_hours: int) -> None:
    if document.is_pinned:
        document.expires_at = None
        return
    document.expires_at = _utcnow_naive() + timedelta(hours=retention_hours)


def _build_artifact_plan(primary_provider: str) -> list[str]:
    providers = [primary_provider]
    if (
        settings.DOCUMENT_DUAL_INDEX_ENABLED
        and primary_provider != DOCUMENT_PROVIDER_OPENAI
        and _provider_is_enabled(DOCUMENT_PROVIDER_OPENAI)
    ):
        providers.append(DOCUMENT_PROVIDER_OPENAI)
    return list(dict.fromkeys(providers))


async def _load_document_for_user(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    document_id: uuid.UUID,
) -> UserDocument | None:
    return (
        await session.exec(
            select(UserDocument)
            .where(
                UserDocument.id == document_id,
                UserDocument.user_id == user_id,
                UserDocument.deleted_at.is_(None),
            )
            .options(selectinload(UserDocument.provider_artifacts))
        )
    ).first()


def _ensure_artifact(document: UserDocument, provider: str) -> DocumentProviderArtifact:
    for artifact in document.provider_artifacts:
        if artifact.provider == provider and artifact.deleted_at is None:
            return artifact
    artifact = DocumentProviderArtifact(
        document_id=document.id,
        provider=provider,
        status=DocumentProviderArtifactStatus.uploading.value,
    )
    document.provider_artifacts.append(artifact)
    return artifact


async def upload_document(
    *,
    session: AsyncSession,
    user: AppUser,
    background_tasks: BackgroundTasks,
    upload: UploadFile,
    provider_override: str | None = None,
) -> UserDocumentResponse:
    original_filename = (upload.filename or "document").strip() or "document"
    _validate_extension(original_filename)
    filename = _build_display_filename(
        original_filename=original_filename,
        user=user,
        uploaded_at=_utcnow_naive(),
    )

    capabilities = await get_document_capabilities(session, user)
    if capabilities.active_doc_count >= capabilities.max_active_docs:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "documents_active_limit_reached",
                "max_active_docs": capabilities.max_active_docs,
            },
        )

    effective_provider, requested_provider, used_fallback = _resolve_document_provider(
        user=user,
        provider_override=provider_override,
    )
    if used_fallback:
        _track_provider_fallback(user, requested_provider, effective_provider)

    tmp_path, size_bytes, sha256 = await _persist_upload_to_temp_file(upload, filename)
    if size_bytes <= 0:
        _cleanup_temp_path(tmp_path)
        raise HTTPException(status_code=400, detail={"error": "empty_document"})

    if size_bytes > capabilities.max_file_size_bytes:
        _cleanup_temp_path(tmp_path)
        raise HTTPException(
            status_code=409,
            detail={
                "error": "document_file_too_large",
                "size_bytes": size_bytes,
                "max_file_size_bytes": capabilities.max_file_size_bytes,
            },
        )

    if (capabilities.used_storage_bytes + size_bytes) > capabilities.max_storage_bytes:
        _cleanup_temp_path(tmp_path)
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
        provider_artifacts=[],
    )
    _refresh_expiration(document, capabilities.doc_retention_hours)
    session.add(document)
    await session.flush()

    artifact_plan = _build_artifact_plan(effective_provider)
    for provider in artifact_plan:
        artifact = _ensure_artifact(document, provider)
        artifact.status = DocumentProviderArtifactStatus.uploading.value
        session.add(artifact)

    _sync_document_from_artifacts(document)
    session.add(document)
    await session.commit()
    await session.refresh(document)

    background_tasks.add_task(_ingest_document_background, document.id, tmp_path, artifact_plan)
    return _document_to_response(document, preferred_provider=effective_provider)


async def _ingest_openai_artifact(
    *,
    document: UserDocument,
    artifact: DocumentProviderArtifact,
    tmp_path: str,
) -> None:
    vector_store = await _openai_client.vector_stores.create(name=f"user-document-{document.id}")
    vector_file = await _openai_client.vector_stores.files.upload_and_poll(
        vector_store_id=vector_store.id,
        file=Path(tmp_path),
    )
    artifact.status = DocumentProviderArtifactStatus.ready.value
    artifact.external_index_id = vector_store.id
    artifact.external_file_id = getattr(vector_file, "file_id", None)
    artifact.error_code = None
    artifact.error_message = None
    artifact.indexed_at = _utcnow_naive()


async def _ingest_document_background(
    document_id: uuid.UUID,
    tmp_path: str,
    artifact_providers: list[str],
) -> None:
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            document = (
                await session.exec(
                    select(UserDocument)
                    .where(UserDocument.id == document_id)
                    .options(selectinload(UserDocument.provider_artifacts))
                )
            ).first()
            if not document:
                return

            for provider in artifact_providers:
                artifact = _ensure_artifact(document, provider)
                artifact.status = DocumentProviderArtifactStatus.processing.value
                artifact.error_code = None
                artifact.error_message = None
                session.add(artifact)
            _sync_document_from_artifacts(document)
            session.add(document)
            await session.commit()

            for provider in artifact_providers:
                artifact = _ensure_artifact(document, provider)
                try:
                    if provider == DOCUMENT_PROVIDER_OPENAI:
                        await _ingest_openai_artifact(document=document, artifact=artifact, tmp_path=tmp_path)
                    else:
                        artifact.status = DocumentProviderArtifactStatus.failed.value
                        artifact.error_code = "document_provider_not_implemented"
                        artifact.error_message = f"Document provider '{provider}' is not implemented."
                except Exception as exc:
                    artifact.status = DocumentProviderArtifactStatus.failed.value
                    artifact.error_code = f"{provider}_ingest_failed"
                    artifact.error_message = str(exc)[:1000]
                session.add(artifact)

            _sync_document_from_artifacts(document)
            session.add(document)
            await session.commit()
    finally:
        _cleanup_temp_path(tmp_path)


async def delete_document(
    *,
    session: AsyncSession,
    user: AppUser,
    document_id: uuid.UUID,
    background_tasks: BackgroundTasks,
) -> None:
    document = await _load_document_for_user(session, user_id=user.id, document_id=document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    document.status = DOCUMENT_STATUS_DELETE_QUEUED
    for artifact in document.provider_artifacts:
        if artifact.deleted_at is None and artifact.status != DocumentProviderArtifactStatus.deleted.value:
            artifact.status = DocumentProviderArtifactStatus.delete_queued.value
            session.add(artifact)
    session.add(document)

    links = (
        await session.exec(
            select(ConversationDocument).where(ConversationDocument.document_id == document.id)
        )
    ).all()
    for link in links:
        await session.delete(link)

    await session.commit()
    background_tasks.add_task(_delete_document_background, document.id)


async def _delete_document_background(document_id: uuid.UUID) -> None:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        document = (
            await session.exec(
                select(UserDocument)
                .where(UserDocument.id == document_id)
                .options(selectinload(UserDocument.provider_artifacts))
            )
        ).first()
        if not document or document.deleted_at is not None:
            return

        try:
            for artifact in document.provider_artifacts:
                if artifact.deleted_at is not None or artifact.status == DocumentProviderArtifactStatus.deleted.value:
                    continue
                if artifact.provider == DOCUMENT_PROVIDER_OPENAI:
                    if artifact.external_index_id:
                        await _openai_client.vector_stores.delete(vector_store_id=artifact.external_index_id)
                    if artifact.external_file_id:
                        await _openai_client.files.delete(file_id=artifact.external_file_id)
                artifact.status = DocumentProviderArtifactStatus.deleted.value
                artifact.deleted_at = _utcnow_naive()
                artifact.external_file_id = None
                artifact.external_index_id = None
                session.add(artifact)
        except Exception as exc:
            document.status = DOCUMENT_STATUS_FAILED
            document.error_code = "document_delete_failed"
            document.error_message = str(exc)[:1000]
            session.add(document)
            await session.commit()
            return

        document.status = DOCUMENT_STATUS_DELETED
        document.deleted_at = _utcnow_naive()
        document.openai_file_id = None
        document.openai_vector_store_id = None
        document.error_code = None
        document.error_message = None
        session.add(document)
        await session.commit()


async def set_document_pin_state(
    *,
    session: AsyncSession,
    user: AppUser,
    document_id: uuid.UUID,
    pin: bool,
) -> UserDocumentResponse:
    document = await _load_document_for_user(session, user_id=user.id, document_id=document_id)
    if not document:
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


def _document_has_attachable_state(document: UserDocument, provider: str) -> bool:
    artifacts = _active_provider_artifacts(document)
    if not artifacts:
        return document.status in _ATTACHABLE_STATUSES
    if any(
        artifact.provider == provider and artifact.status in _ATTACHABLE_STATUSES
        for artifact in artifacts
    ):
        return True
    if provider != DOCUMENT_PROVIDER_OPENAI and settings.DOCUMENT_PROVIDER_FALLBACK_ENABLED:
        return any(
            artifact.provider == DOCUMENT_PROVIDER_OPENAI and artifact.status in _ATTACHABLE_STATUSES
            for artifact in artifacts
        )
    return False


async def replace_conversation_documents(
    *,
    session: AsyncSession,
    user: AppUser,
    conversation_id: uuid.UUID,
    document_ids: list[uuid.UUID],
    provider_override: str | None = None,
) -> ConversationDocumentsUpdateResponse:
    conversation = await session.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    effective_provider, requested_provider, used_fallback = _resolve_document_provider(
        user=user,
        provider_override=provider_override,
    )
    if used_fallback:
        _track_provider_fallback(user, requested_provider, effective_provider)

    normalized_ids = list(dict.fromkeys(document_ids))
    caps: DocumentCapabilitiesResponse | None = None
    docs_to_refresh: list[UserDocument] = []

    if normalized_ids:
        docs = (
            await session.exec(
                select(UserDocument)
                .where(
                    UserDocument.id.in_(normalized_ids),
                    UserDocument.user_id == user.id,
                    UserDocument.deleted_at.is_(None),
                )
                .options(selectinload(UserDocument.provider_artifacts))
            )
        ).all()
        found_ids = {doc.id for doc in docs if _document_has_attachable_state(doc, effective_provider)}
        missing = [doc_id for doc_id in normalized_ids if doc_id not in found_ids]
        if missing:
            raise HTTPException(
                status_code=400,
                detail={"error": "documents_not_ready_or_not_owned", "document_ids": [str(x) for x in missing]},
            )
        caps = await get_document_capabilities(session, user)
        docs_to_refresh = docs

    existing_links = (
        await session.exec(
            select(ConversationDocument).where(ConversationDocument.conversation_id == conversation_id)
        )
    ).all()
    for link in existing_links:
        await session.delete(link)
    await session.flush()

    now = _utcnow_naive()
    for doc_id in normalized_ids:
        session.add(ConversationDocument(conversation_id=conversation_id, document_id=doc_id, attached_at=now))

    if normalized_ids and caps is not None:
        for doc in docs_to_refresh:
            _refresh_expiration(doc, caps.doc_retention_hours)
            session.add(doc)

    await session.commit()
    return ConversationDocumentsUpdateResponse(
        conversation_id=conversation_id,
        document_ids=normalized_ids,
        effective_provider=effective_provider,  # type: ignore[arg-type]
    )


async def list_conversation_document_ids(
    *,
    session: AsyncSession,
    user: AppUser,
    conversation_id: uuid.UUID,
    provider_override: str | None = None,
) -> ConversationDocumentsUpdateResponse:
    conversation = await session.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    effective_provider, requested_provider, used_fallback = _resolve_document_provider(
        user=user,
        provider_override=provider_override,
        strict=False,
    )
    if used_fallback:
        _track_provider_fallback(user, requested_provider, effective_provider)

    links = (
        await session.exec(
            select(ConversationDocument.document_id)
            .join(UserDocument, UserDocument.id == ConversationDocument.document_id)
            .where(
                ConversationDocument.conversation_id == conversation_id,
                UserDocument.deleted_at.is_(None),
            )
        )
    ).all()

    unique_ids: list[uuid.UUID] = []
    for doc_id in links:
        if doc_id and doc_id not in unique_ids:
            unique_ids.append(doc_id)

    return ConversationDocumentsUpdateResponse(
        conversation_id=conversation_id,
        document_ids=unique_ids,
        effective_provider=effective_provider,  # type: ignore[arg-type]
    )


async def list_conversation_ready_vector_store_ids(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    user: AppUser | None = None,
    provider_override: str | None = None,
) -> list[str]:
    effective_provider, requested_provider, used_fallback = _resolve_document_provider(
        user=user,
        provider_override=provider_override,
        strict=False,
    )
    provider_for_search = effective_provider
    if provider_for_search != DOCUMENT_PROVIDER_OPENAI:
        if not settings.DOCUMENT_PROVIDER_FALLBACK_ENABLED:
            return []
        provider_for_search = DOCUMENT_PROVIDER_OPENAI
        _track_provider_fallback(user, requested_provider, provider_for_search)
    elif used_fallback:
        _track_provider_fallback(user, requested_provider, provider_for_search)

    artifact_rows = (
        await session.exec(
            select(DocumentProviderArtifact.external_index_id)
            .join(UserDocument, UserDocument.id == DocumentProviderArtifact.document_id)
            .join(ConversationDocument, ConversationDocument.document_id == UserDocument.id)
            .where(
                ConversationDocument.conversation_id == conversation_id,
                UserDocument.deleted_at.is_(None),
                DocumentProviderArtifact.deleted_at.is_(None),
                DocumentProviderArtifact.provider == provider_for_search,
                DocumentProviderArtifact.status == DocumentProviderArtifactStatus.ready.value,
                DocumentProviderArtifact.external_index_id.is_not(None),
            )
        )
    ).all()
    legacy_rows = (
        await session.exec(
            select(UserDocument.openai_vector_store_id)
            .join(ConversationDocument, ConversationDocument.document_id == UserDocument.id)
            .where(
                ConversationDocument.conversation_id == conversation_id,
                UserDocument.deleted_at.is_(None),
                UserDocument.status == DOCUMENT_STATUS_READY,
                UserDocument.openai_vector_store_id.is_not(None),
            )
        )
    ).all()

    out: list[str] = []
    for row in [*artifact_rows, *legacy_rows]:
        if row and row not in out:
            out.append(row)
    return out


async def count_conversation_pending_indexing_documents(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    user: AppUser | None = None,
    provider_override: str | None = None,
) -> int:
    effective_provider, requested_provider, used_fallback = _resolve_document_provider(
        user=user,
        provider_override=provider_override,
        strict=False,
    )
    provider_for_count = effective_provider
    if provider_for_count != DOCUMENT_PROVIDER_OPENAI and settings.DOCUMENT_PROVIDER_FALLBACK_ENABLED:
        provider_for_count = DOCUMENT_PROVIDER_OPENAI
        _track_provider_fallback(user, requested_provider, provider_for_count)
    elif used_fallback:
        _track_provider_fallback(user, requested_provider, provider_for_count)

    documents = (
        await session.exec(
            select(UserDocument)
            .join(ConversationDocument, ConversationDocument.document_id == UserDocument.id)
            .where(
                ConversationDocument.conversation_id == conversation_id,
                UserDocument.deleted_at.is_(None),
            )
            .options(selectinload(UserDocument.provider_artifacts))
        )
    ).all()

    count = 0
    for document in documents:
        artifacts = _active_provider_artifacts(document)
        if not artifacts:
            if document.status in _PENDING_INDEXING_STATUSES:
                count += 1
            continue
        if any(
            artifact.provider == provider_for_count and artifact.status in _PENDING_INDEXING_STATUSES
            for artifact in artifacts
        ):
            count += 1
    return count


async def touch_conversation_documents_last_used_in_search(
    session: AsyncSession,
    conversation_id: uuid.UUID,
) -> None:
    links = (
        await session.exec(
            select(ConversationDocument).where(ConversationDocument.conversation_id == conversation_id)
        )
    ).all()
    if not links:
        return

    now = _utcnow_naive()
    doc_ids = [link.document_id for link in links]
    docs = (
        await session.exec(
            select(UserDocument).where(UserDocument.id.in_(doc_ids), UserDocument.deleted_at.is_(None))
        )
    ).all()

    for document in docs:
        document.last_used_in_search = now
        session.add(document)
    await session.commit()
