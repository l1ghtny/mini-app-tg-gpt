from typing import Any, AsyncGenerator

from sqlalchemy.ext.asyncio import  create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings

# DATABASE_URL should be async driver, e.g.:
# postgresql+asyncpg://user:pass@host:port/dbname
engine = create_async_engine(
    settings.DATABASE_URL, echo=False,
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
)



async def get_session() -> AsyncGenerator[AsyncSession, Any]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session