"""Admin reporting tests own their schema, never shared test/public data."""

import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession
from app.core.config import settings
from app.services import admin_dashboard  # noqa: F401


@pytest.fixture(autouse=True)
def rebuild_test_db():
    pass


@pytest_asyncio.fixture
async def db(monkeypatch):
    url = os.environ["TEST_DATABASE_URL"]
    assert "admin_test" in url
    schema = "admin_test_" + uuid.uuid4().hex
    admin = create_async_engine(url, poolclass=NullPool)
    async with admin.begin() as conn:
        await conn.execute(text('CREATE SCHEMA "' + schema + '"'))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": schema + ",public"}},
        execution_options={"schema_translate_map": {None: schema}},
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        assert (await conn.execute(text("SELECT current_schema()"))).scalar() == schema
        assert (await conn.execute(text("SELECT count(*) FROM app_user"))).scalar() == 0
    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_SCOPE", "shared")
    monkeypatch.setattr(settings, "ADMIN_DASHBOARD_USER_IDS", frozenset())
    monkeypatch.setattr(settings, "ADMIN_DASHBOARD_TELEGRAM_IDS", frozenset())
    monkeypatch.setattr(settings, "ADMIN_DASHBOARD_TEST_USER_IDS", frozenset())
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield engine, session
    await engine.dispose()
    async with admin.begin() as conn:
        await conn.execute(text('DROP SCHEMA "' + schema + '" CASCADE'))
    await admin.dispose()
