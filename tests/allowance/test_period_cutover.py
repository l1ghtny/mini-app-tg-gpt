import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.allowance import AllowanceControl, AllowanceAccount
from app.services import allowance as a
from test_subscription_periods import membership


async def set_mode(session, mode):
    control = await session.get(
        AllowanceControl, "subscription_periods", with_for_update=True
    )
    control.period_mode = mode
    session.add(control)
    await session.commit()


@pytest.mark.asyncio
async def test_compatibility_pause_and_switch_preserve_existing_balance(
    db, monkeypatch
):
    s, user, _ = await membership(
        db, monkeypatch, datetime(2026, 9, 22), datetime(2026, 10, 22)
    )
    user_id = user.id
    await set_mode(s, "calendar")
    old = await a.account(s, user.id, now=datetime(2026, 9, 23))
    assert old.period_start == datetime(2026, 9, 1) and old.subscription_anchor is None
    old.spent = 100
    s.add(old)
    await s.commit()
    await set_mode(s, "paused")
    with pytest.raises(HTTPException) as error:
        await a.account(s, user.id, now=datetime(2026, 9, 23))
    assert error.value.status_code == 503 and error.value.headers["Retry-After"] == "30"
    await s.rollback()
    await set_mode(s, "subscription")
    new = await a.account(s, user_id, now=datetime(2026, 9, 23))
    assert new.id == old.id and new.spent == 100
    assert new.period_start == datetime(2026, 9, 22) and new.period_end == datetime(
        2026, 10, 22
    )
    assert len((await s.exec(select(AllowanceAccount))).all()) == 1


@pytest.mark.asyncio
async def test_paused_mode_allows_old_requests_to_settle_but_denies_new_ones(
    db, monkeypatch
):
    _, s, user = db
    user_id = user.id
    request = await a.reserve(
        s,
        user_id=user.id,
        conversation_id=None,
        request_id="before-pause",
        model="gpt-5.6-terra",
        ceiling=100,
    )
    account_id = request.account_id
    await set_mode(s, "paused")
    with pytest.raises(HTTPException):
        await a.reserve(
            s,
            user_id=user.id,
            conversation_id=None,
            request_id="during-pause",
            model="gpt-5.6-terra",
            ceiling=100,
        )
    await s.rollback()
    await a.settle(s, user_id, "before-pause", success=False)
    row = await s.get(AllowanceAccount, account_id)
    assert row.reserved == 0 and row.spent == 0


@pytest.mark.asyncio
async def test_pause_waits_for_admitted_transaction_then_rejects_waiting_reader(db):
    engine, s, user = db
    await a.account(s, user.id)
    started = asyncio.Event()

    async def pause():
        async with AsyncSession(engine) as other:
            await other.execute(text("SET LOCAL lock_timeout = '5s'"))
            started.set()
            await set_mode(other, "paused")

    transition = asyncio.create_task(pause())
    await started.wait()
    await asyncio.sleep(0.1)
    assert not transition.done()
    await s.commit()
    await transition
    with pytest.raises(HTTPException):
        await a.account(s, user.id)


@pytest.mark.asyncio
async def test_missing_control_row_fails_closed(db):
    _, s, user = db
    row = await s.get(AllowanceControl, "subscription_periods")
    await s.delete(row)
    await s.commit()
    with pytest.raises(HTTPException):
        await a.account(s, user.id)


@pytest.mark.asyncio
async def test_operator_cutover_aligns_once_and_prohibits_unsafe_rollback(
    db, monkeypatch
):
    from scripts.release.allowance_period_cutover import transition

    start = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
    s, user, _ = await membership(db, monkeypatch, start, start + timedelta(days=30))
    await set_mode(s, "calendar")
    row = await a.account(s, user.id)
    row.spent = 1234
    s.add(row)
    await s.commit()
    with pytest.raises(RuntimeError, match="Pause both"):
        await transition(s, "enable")
    await s.rollback()
    assert (await transition(s, "pause"))["mode"] == "paused"
    result = await transition(s, "enable")
    assert result["aligned_accounts"] == 1 and result["balance_preservation_verified"]
    assert (await transition(s, "enable"))["already_enabled"]
    with pytest.raises(RuntimeError, match="Never revert"):
        await transition(s, "resume-calendar")


@pytest.mark.asyncio
async def test_operator_abort_reopens_calendar_without_losing_inflight_reservation(db):
    from scripts.release.allowance_period_cutover import transition

    _, s, user = db
    await set_mode(s, "calendar")
    await a.reserve(
        s,
        user_id=user.id,
        conversation_id=None,
        request_id="draining",
        model="gpt-5.6-terra",
        ceiling=100,
    )
    await transition(s, "pause")
    with pytest.raises(RuntimeError, match="still draining"):
        await transition(s, "enable")
    await s.rollback()
    assert (await transition(s, "resume-calendar"))["mode"] == "calendar"
    row = (await s.exec(select(AllowanceAccount))).one()
    assert row.reserved == 100 and row.subscription_anchor is None


@pytest.mark.asyncio
async def test_calendar_compatibility_keeps_reset_contract_for_old_frontend(db, monkeypatch):
    now = datetime.now(UTC).replace(tzinfo=None)
    s, user, _ = await membership(db, monkeypatch, now - timedelta(days=1), now + timedelta(hours=1))
    await set_mode(s, "calendar")
    result = await a.snapshot(s, user.id)
    assert result["resets_at"] == a.period(now)[1].replace(tzinfo=UTC).isoformat()
    assert result["period_end_kind"] == "reset"
