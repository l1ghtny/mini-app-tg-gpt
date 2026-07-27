import os

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.identity_helpers import consume_telegram_link, issue_telegram_link
from app.api.session_helpers import (
    create_browser_session,
    resolve_browser_session,
    revoke_browser_session,
)
from app.core.config import settings
from app.db.models import AppUser


def _test_db_url() -> str:
    value = os.getenv("TEST_DATABASE_URL")
    if not value:
        pytest.skip("TEST_DATABASE_URL is required for integration tests")
    return value


@pytest.mark.asyncio
async def test_browser_session_is_revocable_and_multi_day(monkeypatch):
    monkeypatch.setattr(settings, "BROWSER_SESSION_TTL_DAYS", 30)
    engine = create_async_engine(_test_db_url(), future=True, echo=False)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=None)
        session.add(user)
        await session.commit()
        await session.refresh(user)

        token = await create_browser_session(session, user)
        resolved = await resolve_browser_session(session, token)
        assert resolved is not None
        assert resolved[0].id == user.id
        assert (resolved[1].expires_at - resolved[1].created_at).days == 30

        assert await revoke_browser_session(session, token) is True
        assert await resolve_browser_session(session, token) is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_browser_user_can_link_unclaimed_telegram_identity():
    engine = create_async_engine(_test_db_url(), future=True, echo=False)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=None)
        session.add(user)
        await session.commit()
        await session.refresh(user)

        challenge, token = await issue_telegram_link(session, user)
        consumed = await consume_telegram_link(
            session,
            token=token,
            telegram_id=799123450,
            first_name="Pilot",
            last_name=None,
            username="pilot_link_test",
        )
        assert consumed is not None
        assert consumed.id == challenge.id
        assert consumed.status == "linked"
        await session.refresh(user)
        assert user.telegram_id == 799123450
    await engine.dispose()


@pytest.mark.asyncio
async def test_telegram_link_conflict_never_merges_accounts():
    engine = create_async_engine(_test_db_url(), future=True, echo=False)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        target = AppUser(telegram_id=None)
        existing = AppUser(telegram_id=799123451)
        session.add(target)
        session.add(existing)
        await session.commit()
        await session.refresh(target)

        _, token = await issue_telegram_link(session, target)
        consumed = await consume_telegram_link(
            session,
            token=token,
            telegram_id=799123451,
            first_name="Existing",
            last_name=None,
            username=None,
        )
        assert consumed is not None
        assert consumed.status == "conflict"
        assert consumed.conflicting_user_id == existing.id
        await session.refresh(target)
        assert target.telegram_id is None
    await engine.dispose()
