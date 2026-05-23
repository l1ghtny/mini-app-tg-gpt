import asyncio
import os
import sys
from datetime import datetime, timezone

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.api.document_helpers import _delete_document_background  # noqa: E402
from app.db.database import engine  # noqa: E402
from app.db.models import UserDocument  # noqa: E402


async def main() -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        candidates = (await session.exec(
            select(UserDocument).where(
                UserDocument.deleted_at.is_(None),
                UserDocument.is_pinned == False,
                UserDocument.expires_at.is_not(None),
                UserDocument.expires_at < now,
                UserDocument.status.in_(("ready", "failed")),
            )
        )).all()

    for doc in candidates:
        await _delete_document_background(doc.id)


if __name__ == "__main__":
    asyncio.run(main())

