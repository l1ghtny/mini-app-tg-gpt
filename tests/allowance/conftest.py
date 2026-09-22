"""Each test owns a schema. Never reset the shared test database's public schema."""

import os
import uuid
import pytest
import pytest_asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from app.services import allowance_chat as estimates
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel.ext.asyncio.session import AsyncSession
from app.db.models import AppUser
from app.db.subscription_tiers import SubscriptionTier, UserSubscription
from app.db.allowance import (
    AllowanceControl,
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
            AllowanceControl,
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
        session.add(AllowanceControl(id="subscription_periods", period_mode="subscription"))
        await session.commit()
        yield engine, session, user
    await engine.dispose()
    async with admin.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    await admin.dispose()


@pytest.fixture
def estimate_case(monkeypatch):
    for key in ("R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(key, "unused-test-value")
    monkeypatch.setenv("R2_ENDPOINT", "https://storage.invalid")
    from app.api import chat_helpers

    monkeypatch.setattr(estimates.allowance, "require_enabled", lambda _: None)
    monkeypatch.setattr(
        estimates.allowance,
        "account",
        AsyncMock(
            return_value=SimpleNamespace(
                plan="start",
                granted=1250000,
                spent=0,
                reserved=0,
                luna_granted=290000,
                luna_spent=0,
                luna_reserved=0,
            )
        ),
    )
    monkeypatch.setattr(
        chat_helpers, "_build_history_for_openai", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        chat_helpers, "_resolve_system_prompt", lambda *args: "Be helpful."
    )
    session = SimpleNamespace(
        exec=AsyncMock(return_value=SimpleNamespace(all=lambda: [])), commit=AsyncMock()
    )
    user = SimpleNamespace(id="owned-user")
    conv = SimpleNamespace(
        id="owned-chat", history_summary=None, image_quality="medium"
    )
    req = SimpleNamespace(
        model="gpt-5.6-terra",
        content=[
            SimpleNamespace(
                type="text",
                value="Draw a cup.",
                model_dump=lambda: {"type": "text", "value": "Draw a cup."},
            )
        ],
        tool_choice=["image_generation"],
        required_tool="image_generation",
        reasoning_effort="low",
        thinking=True,
        image_quality="low",
        spend_limit_units=None,
        estimate_reference=None,
    )
    return session, user, conv, req
