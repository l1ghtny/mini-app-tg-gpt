from typing import Any, AsyncGenerator

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings


def _create_db_engine(url: str):
    if settings.TEST_ENV:
        # pytest-asyncio creates a fresh event loop per test. Reusing asyncpg
        # pooled connections across those loops causes false cross-loop
        # failures and can hide the result of background-worker tests.
        return create_async_engine(url, echo=False, poolclass=NullPool)
    return create_async_engine(
        url,
        echo=False,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_size=10,
        max_overflow=20,
        pool_timeout=30,
    )


engine = _create_db_engine(settings.DATABASE_URL)
read_engine = _create_db_engine(settings.DATABASE_READ_URL)


async def get_session() -> AsyncGenerator[AsyncSession, Any]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session


async def get_read_session() -> AsyncGenerator[AsyncSession, Any]:
    async with AsyncSession(read_engine, expire_on_commit=False) as session:
        yield session
