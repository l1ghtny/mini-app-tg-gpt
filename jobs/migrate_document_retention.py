"""Give legacy overdue files one full owner-plan retention period before enforcement."""
import asyncio
import logging
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from sqlalchemy import text
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from app.api.document_helpers import _document_limits_for_user, _refresh_expiration, _utcnow_naive
from app.db.database import engine
from app.db.models import AppUser, UserDocument


async def migrate_batch(session: AsyncSession, batch_size: int = 100) -> int:
    documents = (await session.exec(select(UserDocument).where(
        UserDocument.retention_migrated_at.is_(None),
        UserDocument.deleted_at.is_(None),
        UserDocument.status.not_in(("delete_queued", "deleted")),
    ).order_by(UserDocument.id).limit(batch_size).with_for_update())).all()
    now = _utcnow_naive()
    limits = {}
    for document in documents:
        if document.is_pinned:
            document.expires_at = None
        elif document.expires_at is None or document.expires_at <= now:
            if document.user_id not in limits:
                owner = await session.get(AppUser, document.user_id)
                limits[document.user_id] = (await _document_limits_for_user(session, owner)).doc_retention_hours
            _refresh_expiration(document, limits[document.user_id])
        document.retention_migrated_at = now
        session.add(document)
    await session.commit()
    return len(documents)


async def main() -> None:
    # One production migration owns the shared database; beta never grants grace.
    async with engine.connect() as lock_connection:
        await lock_connection.execute(text("SELECT pg_advisory_lock(9060705)"))
        try:
            total = 0
            while True:
                async with AsyncSession(engine, expire_on_commit=False) as session:
                    count = await migrate_batch(session)
                total += count
                if count == 0:
                    break
            logging.warning("Document retention migration completed: %s files", total)
        finally:
            await lock_connection.execute(text("SELECT pg_advisory_unlock(9060705)"))


if __name__ == "__main__":
    asyncio.run(main())
