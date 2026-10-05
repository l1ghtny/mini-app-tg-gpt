"""Bounded retention cleanup with one-time legacy grace and idempotent retries."""
import asyncio
import logging
import os
import sys
from datetime import timedelta

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.api.document_helpers import _delete_document_background, _queue_document_deletion, _utcnow_naive
from app.core.config import settings
from app.db.database import engine
from app.db.models import UserDocument
from sqlalchemy.orm import selectinload


async def main(batch_size: int = 50) -> None:
    if not settings.DOCUMENT_RETENTION_ENFORCED:
        logging.info("Document retention cleanup is disabled")
        return
    # Drain requests already in flight before removing provider data. New
    # attachment/retrieval is blocked at expiry, independently of cleanup timing.
    drain_seconds = max(1800, settings.SHARED_ALLOWANCE_REQUEST_SECONDS + 60)
    cutoff = _utcnow_naive() - timedelta(seconds=drain_seconds)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        ids = (await session.exec(select(UserDocument.id).where(
            UserDocument.deleted_at.is_(None),
            (UserDocument.status == "delete_queued") | (
                UserDocument.retention_migrated_at.is_not(None) & UserDocument.is_pinned.is_(False) & UserDocument.expires_at.is_not(None) &
                (UserDocument.expires_at <= cutoff) & UserDocument.status.in_(("ready", "failed"))
            ),
        ).order_by(UserDocument.updated_at, UserDocument.id).limit(max(1, min(batch_size, 100))))).all()
    for document_id in ids:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            document = (await session.exec(select(UserDocument).where(UserDocument.id == document_id)
                .options(selectinload(UserDocument.provider_artifacts))
                .with_for_update(skip_locked=True).execution_options(populate_existing=True))).first()
            if not document or document.deleted_at is not None:
                continue
            if document.status != "delete_queued":
                if document.retention_migrated_at is None or document.is_pinned or document.expires_at is None or document.expires_at > cutoff:
                    continue
                await _queue_document_deletion(session, document)
                await session.commit()
        await _delete_document_background(document_id)


if __name__ == "__main__":
    asyncio.run(main())
