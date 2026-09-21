"""Each test owns a schema. Never reset the shared test database's public schema."""

import os
import uuid
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel.ext.asyncio.session import AsyncSession
from app.db.models import AppUser
from app.db.subscription_tiers import SubscriptionTier, UserSubscription
from app.db.allowance import (
    AllowanceAccount,
    AllowanceRequest,
    AllowanceEvent,
    ProviderAttempt,
)
from app.core.config import settings


@pytest_asyncio.fixture
async def db(monkeypatch):
    url = os.environ["TEST_DATABASE_URL"]
    assert "pytest" in url or "test" in url
    schema = "allowance_test_" + uuid.uuid4().hex
    admin = create_async_engine(url, poolclass=NullPool)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": schema + ",public"}},
    )
    tables = [
        m.__table__
        for m in (
            AppUser,
            SubscriptionTier,
            UserSubscription,
            AllowanceAccount,
            AllowanceRequest,
            AllowanceEvent,
            ProviderAttempt,
        )
    ]
    async with engine.begin() as conn:
        for table in tables:
            await conn.run_sync(lambda sync_conn, t=table: t.create(sync_conn))
    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_ENABLED", True)
    monkeypatch.setattr(settings, "DEPLOYMENT_CHANNEL", "local")
    monkeypatch.setattr(settings, "TEST_ENV", True)
    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_BETA_PLAN", "start")
    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_PROVIDER_BUDGET_UNITS", 25_000_000)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(default_prompt="Test assistant")
        session.add(user)
        await session.commit()
        yield engine, session, user
    await engine.dispose()
    async with admin.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    await admin.dispose()
