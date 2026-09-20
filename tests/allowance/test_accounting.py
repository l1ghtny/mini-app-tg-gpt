import asyncio
from datetime import UTC, datetime, timedelta
import pytest
from fastapi import HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from app.db.allowance import AllowanceAccount, AllowanceEvent
from app.services import allowance as a
from app.services.allowance_policy import usage_units, step_budget, LUNA


@pytest.mark.asyncio
async def test_crash_releases_customer_hold_but_keeps_unknown_supplier_exposure(
    db, monkeypatch
):
    from app.db.allowance import ProviderAttempt

    _, s, u = db
    r = await a.reserve(
        s,
        user_id=u.id,
        conversation_id=None,
        request_id="crash",
        model=LUNA,
        ceiling=100,
    )
    attempt = await a.begin_attempt(
        s, user_id=u.id, request_id="crash", step_key="1", model=LUNA, budget=80
    )
    await a.identify_attempt(s, attempt, "provider-id-before-disconnect")
    r.created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    s.add(r)
    await s.commit()
    state = await a.snapshot(s, u.id)
    assert state["reserved_units"] == 0 and state["consumed_units"] == 0
    assert (await s.get(ProviderAttempt, attempt)).supplier_units is None
    assert (
        await s.get(ProviderAttempt, attempt)
    ).provider_id == "provider-id-before-disconnect"
    monkeypatch.setattr(a.settings, "SHARED_ALLOWANCE_PROVIDER_BUDGET_UNITS", 90)
    await a.reserve(
        s, user_id=u.id, conversation_id=None, request_id="next", model=LUNA, ceiling=50
    )
    with pytest.raises(HTTPException) as exc:
        await a.begin_attempt(
            s, user_id=u.id, request_id="next", step_key="1", model=LUNA, budget=20
        )
    assert exc.value.status_code == 503


def test_flare_usage_counts_cache_and_output_once():
    from app.services.allowance_images import image_usage_units

    assert (
        image_usage_units(
            {
                "input_tokens_details": {
                    "text_tokens": 100,
                    "image_tokens": 200,
                    "cached_tokens_details": {"text_tokens": 40, "image_tokens": 100},
                },
                "output_tokens": 300,
            }
        )
        == 10350
    )


@pytest.mark.asyncio
async def test_concurrent_final_balance_has_one_winner(db):
    engine, session, user = db
    row = await a.account(session, user.id)
    row.granted = 100
    await session.commit()

    async def reserve(key):
        async with AsyncSession(engine, expire_on_commit=False) as s:
            try:
                return await a.reserve(
                    s,
                    user_id=user.id,
                    conversation_id=None,
                    request_id=key,
                    model="gpt-5.6-terra",
                    ceiling=80,
                )
            except HTTPException as exc:
                await s.rollback()
                return exc.status_code

    results = await asyncio.gather(reserve("a"), reserve("b"))
    assert sum(isinstance(x, int) and x == 402 for x in results) == 1
    await session.refresh(row)
    assert row.reserved == 80


@pytest.mark.asyncio
async def test_exactly_once_and_actual_usage_release(db):
    _, s, u = db
    r = await a.reserve(
        s,
        user_id=u.id,
        conversation_id=None,
        request_id="req",
        model="gpt-5.6-terra",
        ceiling=100_000,
    )
    attempt = await a.begin_attempt(
        s,
        user_id=u.id,
        request_id="req",
        step_key="1",
        model="gpt-5.6-terra",
        budget=100_000,
    )
    await a.finish_attempt(
        s, attempt, units=12_000, usage={"input_tokens": 1000, "output_tokens": 500}
    )
    await a.finish_attempt(s, attempt, units=99_000, usage={})
    await a.settle(s, u.id, "req", success=True)
    await a.settle(s, u.id, "req", success=True)
    state = await a.snapshot(s, u.id)
    assert state["consumed_units"] == 12_000
    assert state["reserved_units"] == 0
    events = (
        await s.exec(select(AllowanceEvent).where(AllowanceEvent.request_id == r.id))
    ).all()
    assert len(events) == 2


@pytest.mark.asyncio
async def test_unknown_usage_keeps_pending_reservation(db):
    _, s, u = db
    await a.reserve(
        s,
        user_id=u.id,
        conversation_id=None,
        request_id="unknown",
        model=LUNA,
        ceiling=10_000,
    )
    attempt = await a.begin_attempt(
        s, user_id=u.id, request_id="unknown", step_key="1", model=LUNA, budget=5000
    )
    await a.settle(s, u.id, "unknown", success=False)
    state = await a.snapshot(s, u.id)
    assert state["reserved_units"] == 10_000
    assert state["history"][0]["status"] == "pending"
    await a.finish_attempt(s, attempt, units=4000, usage={}, success=False)
    await a.settle(s, u.id, "unknown", success=False)
    assert (await a.snapshot(s, u.id))["reserved_units"] == 0


@pytest.mark.asyncio
async def test_child_calls_cannot_exceed_approved_cap(db):
    _, s, u = db
    await a.reserve(
        s, user_id=u.id, conversation_id=None, request_id="r", model=LUNA, ceiling=100
    )
    first = await a.begin_attempt(
        s, user_id=u.id, request_id="r", step_key="1", model=LUNA, budget=80
    )
    await a.finish_attempt(s, first, units=60, usage={})
    with pytest.raises(HTTPException) as exc:
        await a.begin_attempt(
            s, user_id=u.id, request_id="r", step_key="2", model=LUNA, budget=50
        )
    assert exc.value.status_code == 402
    await s.rollback()


@pytest.mark.asyncio
async def test_failed_response_waives_customer_not_supplier(db):
    _, s, u = db
    await a.reserve(
        s, user_id=u.id, conversation_id=None, request_id="r", model=LUNA, ceiling=100
    )
    first = await a.begin_attempt(
        s, user_id=u.id, request_id="r", step_key="1", model=LUNA, budget=100
    )
    await a.finish_attempt(s, first, units=60, usage={})
    await a.settle(s, u.id, "r", success=False)
    assert (await a.snapshot(s, u.id))["consumed_units"] == 0


@pytest.mark.asyncio
async def test_rollover_settles_original_period(db):
    _, s, u = db
    r = await a.reserve(
        s, user_id=u.id, conversation_id=None, request_id="r", model=LUNA, ceiling=100
    )
    p = await a.begin_attempt(
        s, user_id=u.id, request_id="r", step_key="1", model=LUNA, budget=100
    )
    future = await a.account(s, u.id, now=datetime(2027, 1, 1))
    await s.commit()
    await a.finish_attempt(s, p, units=50, usage={})
    await a.settle(s, u.id, "r", success=True)
    await s.refresh(future)
    assert future.spent == 0 and future.reserved == 0
    original = await s.get(AllowanceAccount, r.account_id)
    assert original.spent == 50


@pytest.mark.asyncio
async def test_luna_separate_and_duplicate_request(db):
    _, s, u = db
    await a.reserve(
        s,
        user_id=u.id,
        conversation_id=None,
        request_id="r",
        model=LUNA,
        ceiling=0,
        luna_ceiling=100,
    )
    p = await a.begin_attempt(
        s,
        user_id=u.id,
        request_id="r",
        step_key="1",
        model=LUNA,
        budget=80,
        included=True,
    )
    await a.finish_attempt(s, p, units=50, usage={})
    await a.settle(s, u.id, "r", success=True)
    state = await a.snapshot(s, u.id)
    assert state["consumed_units"] == 0
    with pytest.raises(HTTPException):
        await a.reserve(
            s, user_id=u.id, conversation_id=None, request_id="r", model=LUNA, ceiling=0
        )
    await s.rollback()


@pytest.mark.asyncio
async def test_global_spend_guard(db, monkeypatch):
    _, s, u = db
    monkeypatch.setattr(a.settings, "SHARED_ALLOWANCE_PROVIDER_BUDGET_UNITS", 50)
    await a.reserve(
        s, user_id=u.id, conversation_id=None, request_id="r", model=LUNA, ceiling=100
    )
    with pytest.raises(HTTPException) as exc:
        await a.begin_attempt(
            s, user_id=u.id, request_id="r", step_key="1", model=LUNA, budget=60
        )
    assert exc.value.status_code == 503
    await s.rollback()


def test_reasoning_is_not_double_billed():
    assert (
        usage_units(
            "gpt-5.6-terra",
            {"input_tokens": 1000, "output_tokens": 500, "reasoning_tokens": 400},
        )
        == 8000
    )
    assert (
        usage_units(
            "gpt-5.6-terra",
            {
                "input_tokens": 1000,
                "cached_tokens": 200,
                "cache_write_tokens": 300,
                "output_tokens": 500,
            },
        )
        == 7790
    )


def test_bad_usage_rejected_and_image_budget_does_not_count_base64_text():
    with pytest.raises(ValueError):
        usage_units(LUNA, {"input_tokens": 2, "cached_tokens": 3})
    a = step_budget(
        LUNA,
        [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_image",
                        "image_url": "data:image/png;base64," + "A" * 100_000,
                    }
                ],
            }
        ],
    )
    b = step_budget(
        LUNA,
        [
            {
                "role": "user",
                "content": [{"type": "input_image", "image_url": "https://test/image"}],
            }
        ],
    )
    assert a == b
