from __future__ import annotations
import asyncio
from datetime import datetime, timedelta, UTC

from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import DerivedImage, MessageContent
from app.r2.methods import delete_object, head_object
from app.r2.settings import Settings
from app.r2.client import R2_BUCKET
from app.services.background.image_deriver import _public_url  # reuse internal helper

# Safety: only delete originals older than this many hours since derivation
DEFAULT_AGE_HOURS = 24

async def _delete_original_if_unused(session: AsyncSession, row: DerivedImage) -> bool:
    """
    If no MessageContent rows still reference the original public URL,
    and the object exists, delete the original from R2.
    Returns True if deleted, False otherwise.
    """
    original_url = _public_url(row.original_key)

    # Any DB references still pointing at the original?
    ref = await session.exec(
        select(MessageContent.id).where(
            MessageContent.type == "image_url",
            MessageContent.value == original_url,
        ).limit(1)
    )
    if ref.first():
        return False  # still referenced; skip

    # Double-check the object exists; HEAD may raise if missing
    try:
        await head_object(row.original_key)
    except Exception:
        return False  # already gone (or different bucket/prefix); nothing to do

    # Safe to delete
    await delete_object(row.original_key)
    return True

async def cleanup_derived_images(
    database_url: str,
    *,
    older_than_hours: int = DEFAULT_AGE_HOURS,
    limit: int = 1000,
    dry_run: bool = False,
) -> tuple[int, int]:
    """
    Scan DerivedImage rows older than N hours, and delete original R2 objects
    when they are no longer referenced by MessageContent.
    Returns (scanned, deleted).
    """
    engine = create_async_engine(database_url, future=True, echo=False)
    scanned = deleted = 0
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        cutoff = datetime.now(UTC) - timedelta(hours=older_than_hours)
        rows = (await session.exec(
            select(DerivedImage)
            .where(DerivedImage.created_at <= cutoff.replace(tzinfo=None))
            .limit(limit)
        )).all()

        for row in rows:
            scanned += 1
            if dry_run:
                continue
            try:
                if await _delete_original_if_unused(session, row):
                    deleted += 1
            except Exception:
                # optionally log
                pass

    await engine.dispose()
    return scanned, deleted

if __name__ == "__main__":
    import os
    db_url = os.getenv("DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not db_url:
        raise SystemExit("DATABASE_URL or TEST_DATABASE_URL required")
    dry = os.getenv("DRY_RUN", "0") == "1"
    hours = int(os.getenv("CLEANUP_AGE_HOURS", str(DEFAULT_AGE_HOURS)))
    scan, rm = asyncio.run(cleanup_derived_images(db_url, older_than_hours=hours, dry_run=dry))
    print(f"Scanned: {scan}, Deleted originals: {rm} (older_than_hours={hours}, dry_run={dry})")
