"""Private chat results with durable publication, revision ownership and expiry."""
import asyncio
import hashlib
import json
import logging
import re
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import HTTPException
from sqlmodel import select, func
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.db.database import engine
from app.db.models import AppUser, ChatDocument, Conversation
from app.r2 import private_documents as storage
from app.schemas.chat_documents import CreateDocumentArguments, DocumentSpec
from app.services.chat_document_renderer import MIME_TYPES, MAX_FILE_BYTES, render_document

logger = logging.getLogger(__name__)


def now():
    return datetime.now(UTC).replace(tzinfo=None)


def public_document(row):
    return dict(id=str(row.id), filename=row.filename, format=row.format,
                size_bytes=row.size_bytes, version=row.version,
                parent_id=str(row.parent_id) if row.parent_id else None,
                expires_at=row.expires_at.replace(tzinfo=UTC).isoformat())


async def owned_document(session, user_id, document_id, *, conversation_id=None):
    row = (await session.exec(select(ChatDocument).where(
        ChatDocument.id == document_id, ChatDocument.user_id == user_id,
        ChatDocument.deleted_at.is_(None),
    ))).first()
    if not row or (conversation_id is not None and row.conversation_id != conversation_id):
        raise HTTPException(404, detail="document_not_found")
    conversation = await session.get(Conversation, row.conversation_id)
    if not conversation or conversation.user_id != user_id:
        raise HTTPException(404, detail="document_not_found")
    if row.status != "ready" or row.expires_at <= now():
        raise HTTPException(410, detail="document_expired_or_unavailable")
    return row


async def create_chat_document(run, args):
    if not settings.CHAT_DOCUMENT_GENERATION_ENABLED:
        raise HTTPException(409, detail="document_generation_unavailable")
    arguments = CreateDocumentArguments.model_validate(args)
    spec = DocumentSpec(title=arguments.title, blocks=arguments.blocks)
    digest = hashlib.sha256(json.dumps(arguments.model_dump(), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    request_key = f"{run.user_id}:{run.request_id}:{digest}"
    data = await asyncio.to_thread(render_document, spec, arguments.format)
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(413, detail="generated_document_too_large")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        # Shares the account deletion/admission lock. Deletion cannot erase an
        # identity while a private object is being published for that identity.
        user = (await session.exec(select(AppUser).where(AppUser.id == run.user_id).with_for_update())).first()
        if not user or user.deleted_at is not None:
            raise HTTPException(404, detail="account_not_found")
        conversation = (await session.exec(select(Conversation).where(Conversation.id == run.conversation_id).with_for_update())).first()
        if not conversation or conversation.user_id != user.id:
            raise HTTPException(404, detail="conversation_not_found")
        existing = (await session.exec(select(ChatDocument).where(ChatDocument.request_key == request_key))).first()
        if existing:
            return public_document(await owned_document(session, user.id, existing.id, conversation_id=conversation.id))
        parent = None
        if arguments.parent_document_id:
            parent = await owned_document(session, user.id, uuid.UUID(arguments.parent_document_id), conversation_id=conversation.id)
            content = json.dumps(parent.spec, ensure_ascii=False)
            ranges = sorted(run.document_read_ranges.get(str(parent.id), []))
            end = 0
            for start, stop in ranges:
                if start > end:
                    break
                end = max(end, stop)
            if end < len(content):
                raise ValueError("Read the complete source document before revising it")
        count, total_bytes = (await session.exec(select(func.count(ChatDocument.id), func.coalesce(func.sum(ChatDocument.size_bytes), 0)).where(
            ChatDocument.user_id == user.id, ChatDocument.deleted_at.is_(None),
        ))).one()
        if count >= 40 or total_bytes + len(data) > 20_000_000:
            raise HTTPException(409, detail="generated_document_storage_full")
        row = ChatDocument(user_id=user.id, conversation_id=conversation.id,
            request_key=request_key, filename=re.sub(r'[\\/:*?"<>|]', '_', arguments.title.strip()[:120]) + f".{arguments.format}",
            format=arguments.format, spec=spec.model_dump(), parent_id=parent.id if parent else None,
            version=parent.version + 1 if parent else 1, size_bytes=len(data),
            bucket=storage.get_private_documents_bucket() or "", key="", expires_at=now() + timedelta(days=5))
        if not row.bucket:
            raise HTTPException(503, detail="document_storage_unavailable")
        # Only UUIDs enter storage paths; model-authored filenames never do.
        row.key = f"chat-documents/{user.id}/{row.id}/result.{row.format}"
        session.add(row)
        await session.flush()
        try:
            with tempfile.TemporaryDirectory(prefix="lightny-document-") as directory:
                path = Path(directory) / f"result.{row.format}"
                path.write_bytes(data)
                async with asyncio.timeout(20):
                    await storage.upload_document_source(bucket=row.bucket, key=row.key, path=str(path), content_type=MIME_TYPES[row.format], metadata={"sha256": hashlib.sha256(data).hexdigest()})
            row.status = "ready"
        except BaseException:
            # A timed-out PUT can complete upstream. Retain its exact key for
            # cleanup rather than lose the object when the transaction rolls back.
            row.status = "failed"
            row.expires_at = now()
            await session.commit()
            raise
        await session.commit()
        return public_document(row)


async def read_chat_document(run, document_id, offset=0):
    from app.services.shared_chat_loop import result_tokens
    async with AsyncSession(engine) as session:
        row = await owned_document(session, run.user_id, uuid.UUID(document_id), conversation_id=run.conversation_id)
        content = json.dumps(row.spec, ensure_ascii=False)
        if type(offset) is not int or not 0 <= offset < len(content):
            raise ValueError("Invalid document offset")
        high, low, best = min(len(content), offset + 8000), offset + 1, None
        while low <= high:
            end = (low + high) // 2
            value = json.dumps({"document": public_document(row), "content_chunk": content[offset:end],
                "offset": offset, "next_offset": end if end < len(content) else None, "total_chars": len(content)}, ensure_ascii=False)
            if result_tokens(value) <= 2000:
                best = (end, value)
                low = end + 1
            else:
                high = end - 1
        if best is None:
            raise ValueError("Document metadata exceeds the result limit")
        run.document_read_ranges.setdefault(str(row.id), []).append((offset, best[0]))
        return best[1]


async def delete_chat_documents(session, *, user_id=None, batch_size=50):
    query = select(ChatDocument).where(ChatDocument.deleted_at.is_(None))
    query = query.where(ChatDocument.user_id == user_id) if user_id else query.where(
        (ChatDocument.expires_at <= now()) | ChatDocument.conversation_id.not_in(select(Conversation.id)))
    rows = (await session.exec(query.order_by(ChatDocument.cleanup_attempted_at.asc().nulls_first(), ChatDocument.expires_at, ChatDocument.id).limit(batch_size).with_for_update(skip_locked=user_id is None))).all()
    deleted = 0
    for row in rows:
        row.cleanup_attempted_at = now()
        session.add(row)
        try:
            async with asyncio.timeout(20):
                await storage.delete_document_source(bucket=row.bucket, key=row.key)
        except Exception:
            if user_id is not None:
                raise
            logger.exception("Generated document cleanup failed document_id=%s", row.id)
            continue
        row.spec = {}
        row.deleted_at = now()
        row.status = "deleted"
        session.add(row)
        deleted += 1
    return deleted
