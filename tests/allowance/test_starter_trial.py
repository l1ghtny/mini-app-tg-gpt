import asyncio
from datetime import datetime, timedelta, UTC
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from app.db.allowance import AllowanceEvent, AllowanceAccount
from app.services import allowance as a


async def enroll(db, monkeypatch):
    engine, session, user = db
    monkeypatch.setattr(a.settings, "SHARED_ALLOWANCE_TRIAL_ENABLED", True)
    row = await a.account(session, user.id)
    await session.commit()
    return engine, session, user, row


async def finish(s, user, key, success=True, luna=False):
    model = "gpt-5.6-luna" if luna else "claude-sonnet-5"
    await a.reserve(s, user_id=user.id, conversation_id=uuid4(), request_id=key, model=model, ceiling=1000, luna_ceiling=1000 if luna else 0)
    attempt = await a.begin_attempt(s, user_id=user.id, request_id=key, step_key="1", model=model, budget=1000, included=luna)
    await a.finish_attempt(s, attempt, units=100, usage={})
    await a.settle(s, user.id, key, success=success)


@pytest.mark.asyncio
async def test_ready_trial_is_one_lifetime_grant_across_months_and_scopes(db, monkeypatch):
    _, s, user, row = await enroll(db, monkeypatch)
    assert row.granted == 500_000 and row.luna_granted == 100_000
    state = await a.snapshot(s, user.id)
    assert state["trial"]["state"] == "ready" and state["resets_at"] is None
    assert state["trial"]["expires_at"] is None and state["default_model"] == "claude-sonnet-5"
    assert len(state["models"]) == 7 and len(state["plans"]) == 4
    monkeypatch.setattr(a.settings, "SHARED_ALLOWANCE_SCOPE", "another-surface")
    assert (await a.account(s, user.id, now=datetime(2027, 1, 1))).id == row.id
    await s.commit()
    await finish(s, user, "other-scope")
    assert (await a.snapshot(s, user.id))["consumed_units"] == 100
    assert len((await s.exec(select(AllowanceAccount))).all()) == 1


@pytest.mark.asyncio
async def test_failure_does_not_start_clock_or_charge_either_pool(db, monkeypatch):
    _, s, user, row = await enroll(db, monkeypatch)
    await finish(s, user, "failed-luna", success=False, luna=True)
    await s.refresh(row)
    assert row.trial_started_at is None and row.spent == 0 and row.luna_spent == 0
    assert row.reserved == 0 and row.luna_reserved == 0


@pytest.mark.asyncio
async def test_success_starts_exactly_seven_days_and_expiry_blocks_both_pools(db, monkeypatch):
    _, s, user, row = await enroll(db, monkeypatch)
    await finish(s, user, "first", luna=True)
    await s.refresh(row)
    started = row.trial_started_at
    assert started is not None and row.period_end-started == timedelta(days=7)
    await finish(s, user, "second")
    await a.settle(s, user.id, "first", success=True)
    await s.refresh(row)
    assert row.trial_started_at == started and row.spent == 100
    events = (await s.exec(select(AllowanceEvent).where(AllowanceEvent.kind == "trial_start"))).all()
    assert len(events) == 1
    row.period_end = datetime.now(UTC).replace(tzinfo=None)-timedelta(seconds=1)
    s.add(row); await s.commit()
    state = await a.snapshot(s, user.id)
    assert state["trial"]["state"] == "expired" and not state["luna_available"] and state["available_units"] == 0
    user_id = user.id
    for model in ("gpt-5.6-luna", "claude-sonnet-5"):
        with pytest.raises(HTTPException) as error:
            await a.reserve(s, user_id=user_id, conversation_id=None, request_id=model, model=model, ceiling=0)
        assert error.value.detail["error"] == "trial_expired"
        await s.rollback()


@pytest.mark.asyncio
async def test_simultaneous_first_success_activates_once(db, monkeypatch):
    engine, s, user, row = await enroll(db, monkeypatch)
    for key in ("one", "two"):
        await a.reserve(s, user_id=user.id, conversation_id=None, request_id=key, model="claude-sonnet-5", ceiling=100)
    async def settle(key):
        async with AsyncSession(engine, expire_on_commit=False) as other:
            await a.settle(other, user.id, key, success=True)
    await asyncio.gather(settle("one"), settle("two"))
    await s.refresh(row)
    assert row.trial_started_at and row.reserved == 0
    assert len((await s.exec(select(AllowanceEvent).where(AllowanceEvent.kind == "trial_start"))).all()) == 1


@pytest.mark.asyncio
async def test_login_creates_ready_grant_once_without_legacy_subscription(db, monkeypatch):
    from app.api.auth_helpers import ensure_starter_bundle
    _, s, user, row = await enroll(db, monkeypatch)
    assert not await ensure_starter_bundle(s, user)
    assert not await ensure_starter_bundle(s, user)
    await s.refresh(row)
    assert row.trial_started_at is None
    assert len((await s.exec(select(AllowanceAccount))).all()) == 1


@pytest.mark.asyncio
async def test_unknown_billing_does_not_delay_successful_trial_activation(db, monkeypatch):
    _, s, user, row = await enroll(db, monkeypatch)
    await a.reserve(s, user_id=user.id, conversation_id=None, request_id='pending', model='claude-sonnet-5', ceiling=1000)
    attempt = await a.begin_attempt(s, user_id=user.id, request_id='pending', step_key='1', model='claude-sonnet-5', budget=1000)
    await a.settle(s, user.id, 'pending', success=True)
    await s.refresh(row)
    started = row.trial_started_at
    assert started is not None and row.reserved == 1000
    await a.finish_attempt(s, attempt, units=100, usage={})
    await a.settle(s, user.id, 'pending', success=True)
    await s.refresh(row)
    assert row.trial_started_at == started and row.reserved == 0 and row.spent == 100


@pytest.mark.asyncio
async def test_trial_document_capacity(db, monkeypatch):
    for key, value in {"R2_BUCKET": "unused-test-bucket", "R2_ENDPOINT": "https://storage.invalid", "R2_ACCESS_KEY_ID": "test-only", "R2_SECRET_ACCESS_KEY": "test-only"}.items():
        monkeypatch.setenv(key, value)
    from app.api.document_helpers import _document_limits_for_user
    _, s, user, _ = await enroll(db, monkeypatch)
    limits = await _document_limits_for_user(s, user)
    assert limits.max_active_docs == 5 and limits.max_file_size_bytes == 10 * 1024 * 1024
    assert limits.max_storage_bytes == 25 * 1024 * 1024


@pytest.mark.asyncio
async def test_starter_migration_is_repeat_safe_and_retains_activation(db, monkeypatch):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    engine, s, user, row = await enroll(db, monkeypatch)
    await finish(s, user, 'before-migration')
    await s.refresh(row)
    started = row.trial_started_at
    await s.commit()
    path = Path(__file__).parents[2] / 'migrations/versions/xu8c9d0e1f2a_starter_trial_onboarding.py'
    spec = importlib.util.spec_from_file_location('starter_migration', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    def migrate(conn):
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade(); module.upgrade()
    async with engine.begin() as conn:
        await conn.run_sync(migrate)
    await s.refresh(row)
    assert row.trial_started_at == started and row.spent == 100
