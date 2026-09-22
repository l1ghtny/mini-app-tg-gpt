from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.allowance import AllowanceAccount, AllowanceEvent, AllowanceRequest
from app.services import allowance as a
from app.services.allowance_policy import BASE_GRANT, PLANS, RATE_VERSION
from test_private_plans import enroll


@pytest.mark.parametrize(
    "start,end,now,expected",
    [
        (
            datetime(2026, 9, 22, 15),
            datetime(2026, 10, 22, 15),
            datetime(2026, 10, 1),
            (datetime(2026, 9, 22, 15), datetime(2026, 10, 22, 15)),
        ),
        (
            datetime(2026, 1, 31, 15),
            datetime(2026, 3, 2, 15),
            datetime(2026, 2, 28, 16),
            (datetime(2026, 1, 31, 15), datetime(2026, 3, 2, 15)),
        ),
        (
            datetime(2026, 1, 31, 15),
            None,
            datetime(2026, 2, 28, 15),
            (datetime(2026, 2, 28, 15), datetime(2026, 3, 31, 15)),
        ),
        (
            datetime(2028, 1, 31, 15),
            None,
            datetime(2028, 2, 29, 14),
            (datetime(2028, 1, 31, 15), datetime(2028, 2, 29, 15)),
        ),
        (
            datetime(2026, 12, 22, 15),
            datetime(2027, 2, 10),
            datetime(2027, 1, 23),
            (datetime(2027, 1, 22, 15), datetime(2027, 2, 10)),
        ),
    ],
)
def test_subscription_windows(start, end, now, expected):
    assert a.subscription_period(start, end, now) == expected


async def membership(db, monkeypatch, start, end=None, names=None):
    s, user, subs = await enroll(db, monkeypatch, names or ["Close Friends Tier"])
    for sub in subs:
        sub.started_at, sub.expires_at = start, end
        s.add(sub)
    await s.commit()
    return s, user, subs


@pytest.mark.asyncio
async def test_invitation_keeps_balance_across_first_and_expires_without_grant(
    db, monkeypatch
):
    start = datetime(2026, 9, 22, 15)
    end = start + timedelta(days=30)
    s, user, _ = await membership(db, monkeypatch, start, end)
    first = await a.account(s, user.id, now=start)
    first.spent, first.luna_spent = 100, 50
    s.add(first)
    await s.commit()
    after_first = await a.account(s, user.id, now=datetime(2026, 10, 1))
    assert after_first.id == first.id
    assert (after_first.spent, after_first.luna_spent) == (100, 50)
    assert (after_first.period_start, after_first.period_end) == (start, end)
    with pytest.raises(HTTPException) as err:
        await a.account(s, user.id, now=end)
    assert err.value.status_code == 403
    assert len((await s.exec(select(AllowanceAccount))).all()) == 1


@pytest.mark.asyncio
async def test_anniversary_renewal_and_inflight_settlement_stay_in_original_period(
    db, monkeypatch
):
    now = datetime.now(UTC).replace(tzinfo=None)
    s, user, _ = await membership(db, monkeypatch, now - timedelta(days=5))
    row = await a.account(s, user.id)
    request = await a.reserve(
        s,
        user_id=user.id,
        conversation_id=None,
        request_id="inflight",
        model="gpt-5.6-terra",
        ceiling=100,
        luna_ceiling=20,
    )
    next_row = await a.account(s, user.id, now=row.period_end)
    await s.commit()
    assert next_row.id != row.id and next_row.spent == next_row.reserved == 0
    await a.settle(s, user.id, request.request_id, success=False)
    await s.refresh(row)
    assert row.reserved == row.luna_reserved == 0
    assert (await a.request_row(s, user.id, request.request_id)).account_id == row.id


@pytest.mark.asyncio
async def test_cutover_preserves_account_requests_spend_and_holds(db, monkeypatch):
    start = datetime(2026, 9, 22, 15)
    s, user, _ = await membership(db, monkeypatch, start, start + timedelta(days=30))
    row = AllowanceAccount(
        user_id=user.id,
        scope="beta",
        plan="premium",
        rate_version=RATE_VERSION,
        period_start=datetime(2026, 9, 1),
        period_end=datetime(2026, 10, 1),
        granted=BASE_GRANT * 5,
        spent=1234,
        reserved=100,
        luna_granted=PLANS["premium"]["luna_units"],
        luna_spent=50,
        luna_reserved=20,
    )
    s.add(row)
    await s.flush()
    request = AllowanceRequest(
        account_id=row.id,
        user_id=user.id,
        scope="beta",
        request_id="old",
        model="gpt-5.6-terra",
        ceiling=100,
        luna_ceiling=20,
    )
    s.add(request)
    await s.commit()
    # Even when first used after Oct 1, carry the old spend into the invitation.
    current = await a.account(s, user.id, now=datetime(2026, 10, 2))
    await s.commit()
    assert current.id == row.id and current.subscription_anchor == start
    assert (
        current.spent,
        current.reserved,
        current.luna_spent,
        current.luna_reserved,
    ) == (1234, 100, 50, 20)
    assert (current.period_start, current.period_end) == (
        start,
        start + timedelta(days=30),
    )
    again = await a.account(s, user.id, now=datetime(2026, 10, 2))
    await s.commit()
    assert again.id == row.id
    assert (
        len(
            (
                await s.exec(
                    select(AllowanceEvent).where(
                        AllowanceEvent.kind == "period_alignment"
                    )
                )
            ).all()
        )
        == 1
    )
    await a.settle(s, user.id, "old", success=False)
    assert row.spent == 1234 and row.reserved == row.luna_reserved == 0


@pytest.mark.asyncio
async def test_different_start_dates_upgrade_and_downgrade_do_not_refill(
    db, monkeypatch
):
    s, user, subs = await membership(
        db,
        monkeypatch,
        datetime(2026, 9, 10),
        names=["Close Friends Tier", "Katush Tier"],
    )
    subs[1].started_at = datetime(2026, 9, 22)
    subs[1].expires_at = datetime(2026, 10, 1)
    s.add(subs[1])
    await s.commit()
    row = await a.account(s, user.id, now=datetime(2026, 9, 20))
    row.spent = 1000
    s.add(row)
    await s.commit()
    high = await a.account(s, user.id, now=datetime(2026, 9, 23))
    assert high.id == row.id and high.plan == "max" and high.spent == 1000
    assert high.period_end == datetime(2026, 10, 10)
    await s.commit()
    low = await a.account(s, user.id, now=datetime(2026, 10, 1))
    assert low.id == row.id and low.plan == "premium" and low.spent == 1000
    assert low.period_end == datetime(2026, 10, 10)


@pytest.mark.asyncio
async def test_early_extension_waits_for_existing_cycle_and_reactivation_starts_new_one(
    db, monkeypatch
):
    start = datetime(2026, 1, 31, 15)
    end = start + timedelta(days=30)
    s, user, subs = await membership(db, monkeypatch, start, end)
    row = await a.account(s, user.id, now=start)
    row.spent = 100
    subs[0].expires_at = end + timedelta(days=30)
    s.add_all([row, subs[0]])
    await s.commit()
    same = await a.account(s, user.id, now=datetime(2026, 2, 28, 16))
    assert same.id == row.id and same.spent == 100 and same.period_end == end
    new = await a.account(s, user.id, now=end)
    assert new.id != row.id and new.spent == 0 and new.period_start == end
    await s.commit()
    # Expired same-tier code redemption updates started_at, so this is new access.
    subs[0].started_at = datetime(2026, 6, 12, 10)
    subs[0].expires_at = subs[0].started_at + timedelta(days=30)
    s.add(subs[0])
    await s.commit()
    reactivated = await a.account(s, user.id, now=subs[0].started_at)
    assert reactivated.period_start == subs[0].started_at
    assert reactivated.period_end == subs[0].expires_at


@pytest.mark.asyncio
async def test_snapshot_distinguishes_access_expiry_from_reset(db, monkeypatch):
    start = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
    s, user, subs = await membership(db, monkeypatch, start, start + timedelta(days=30))
    result = await a.snapshot(s, user.id)
    assert result["resets_at"] is None
    assert result["period_end_kind"] == "access_expiry"
    assert (
        result["period_ends_at"]
        == result["access_expires_at"]
        == subs[0].expires_at.replace(tzinfo=UTC).isoformat()
    )
    subs[0].expires_at = None
    s.add(subs[0])
    await s.commit()
    result = await a.snapshot(s, user.id)
    assert result["resets_at"] == result["period_ends_at"]
    assert result["period_end_kind"] == "reset" and result["access_expires_at"] is None


@pytest.mark.asyncio
async def test_concurrent_first_access_creates_one_grant(db, monkeypatch):
    import asyncio

    s, user, _ = await membership(db, monkeypatch, datetime(2026, 9, 22))
    engine = db[0]

    async def read():
        async with AsyncSession(engine, expire_on_commit=False) as other:
            row = await a.account(other, user.id, now=datetime(2026, 9, 23))
            await other.commit()
            return row.id

    ids = await asyncio.gather(read(), read())
    assert ids[0] == ids[1]
    assert (
        len(
            (
                await s.exec(
                    select(AllowanceEvent).where(AllowanceEvent.kind == "grant")
                )
            ).all()
        )
        == 1
    )


@pytest.mark.asyncio
async def test_ambiguous_legacy_balances_fail_closed_instead_of_refilling(
    db, monkeypatch
):
    s, user, _ = await membership(db, monkeypatch, datetime(2026, 9, 22))
    for month in (9, 10):
        s.add(
            AllowanceAccount(
                user_id=user.id,
                scope="beta",
                plan="premium",
                rate_version=RATE_VERSION,
                period_start=datetime(2026, month, 1),
                period_end=datetime(2026, month + 1, 1),
                granted=BASE_GRANT * 5,
                spent=1234,
                luna_granted=PLANS["premium"]["luna_units"],
            )
        )
    await s.commit()
    with pytest.raises(HTTPException) as err:
        await a.account(s, user.id, now=datetime(2026, 10, 2))
    assert err.value.detail == {"error": "allowance_period_reconciliation_required"}
    assert len((await s.exec(select(AllowanceAccount))).all()) == 2


@pytest.mark.asyncio
async def test_additive_migration_is_repeatable_and_preserves_balances(db):
    from importlib import import_module
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration = import_module(
        "migrations.versions.xw0e1f2a3b4d_subscription_allowance_periods"
    )
    engine, s, user = db
    row = await a.account(s, user.id)
    row.spent = 123
    s.add(row)
    await s.commit()
    async with engine.begin() as conn:
        await conn.execute(
            text("ALTER TABLE allowance_account DROP COLUMN subscription_anchor")
        )

        def apply(connection):
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                migration.upgrade()
                migration.downgrade()

        await conn.run_sync(apply)
    await s.refresh(row)
    assert row.spent == 123 and row.subscription_anchor is None
