import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.db.allowance import AllowanceEvent, ProviderAttempt
from app.services import allowance, allowance_tasks
from app.services.allowance_task_policy import ITERATIVE, limits
from app.services.generation_budget import execution_plan


async def task(session, user, key, *, cap=100_000, supplier=500_000, recovery=100_000):
    a = await allowance.account(session, user.id)
    saved = dict(
        policy=ITERATIVE,
        generation=execution_plan("gpt-5.6-terra", []).dump(),
        tools=[],
        tool_rounds=6,
        recovery_tokens=24_000,
        image_consent=False,
        supplier_ceiling=supplier,
        risk_policy=limits(a),
        final_answer_units=100_000,
    )
    return await allowance.reserve(
        session,
        user_id=user.id,
        conversation_id=None,
        request_id=key,
        model="gpt-5.6-terra",
        ceiling=cap,
        execution_plan=saved,
        recovery_ceiling=recovery,
    )


async def attempt(
    session, user, key, *, budget=10_000, owner="worker", step="1", recovery=False
):
    return await allowance.begin_attempt(
        session,
        user_id=user.id,
        request_id=key,
        owner=owner,
        step_key=step,
        model="gpt-5.6-terra",
        budget=budget,
        recovery=recovery,
    )


@pytest.mark.asyncio
async def test_two_near_zero_tasks_reverse_settlement_never_creates_debt(
    db, monkeypatch
):
    engine, session, user = db
    user = SimpleNamespace(id=user.id)
    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_GENERATION_V2_ENABLED", True)
    a = await allowance.account(session, user.id)
    a.spent = a.granted - 1
    await session.commit()
    for key in ("first", "second"):
        await task(session, user, key, cap=1)
        await allowance_tasks.claim(session, user.id, key, "worker")
        aid = await attempt(session, user, key)
        await allowance.finish_attempt(session, aid, units=10, usage={})
    assert a.reserved == a.luna_reserved == 0
    with pytest.raises(HTTPException, match="429"):
        await task(session, user, "third", cap=1)
    await session.rollback()
    with pytest.raises(HTTPException, match="409"):
        await task(session, user, "first", cap=1)
    await session.rollback()
    async with AsyncSession(engine, expire_on_commit=False) as other:
        await allowance.settle(other, user.id, "second", success=True)
    await allowance.settle(session, user.id, "first", success=True)
    await allowance.settle(session, user.id, "second", success=True)
    a = await allowance.account(session, user.id)
    assert a.spent == a.granted and a.reserved == 0
    assert (await allowance.request_row(session, user.id, "second")).charged == 1
    assert (await allowance.request_row(session, user.id, "first")).charged == 0
    events = (
        await session.exec(select(AllowanceEvent).where(AllowanceEvent.kind == "admit"))
    ).all()
    assert len(events) == 2 and all(e.units == e.luna_units == 0 for e in events)


@pytest.mark.asyncio
async def test_admission_race_is_serialized_across_workers_and_scopes(db, monkeypatch):
    engine, session, user = db
    user = SimpleNamespace(id=user.id)
    await session.commit()

    async def reserve(key):
        async with AsyncSession(engine, expire_on_commit=False) as other:
            try:
                return await task(other, user, key)
            except HTTPException as exc:
                return exc.status_code

    results = await asyncio.gather(*(reserve(str(i)) for i in range(3)))
    assert results.count(429) == 1
    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_SCOPE", "other-environment")
    with pytest.raises(HTTPException) as exc:
        await task(session, user, "fourth")
    assert exc.value.detail["error"] == "active_task_limit"


@pytest.mark.asyncio
async def test_expired_and_cancelled_owners_cannot_start_or_be_reclaimed(db):
    _, session, user = db
    user = SimpleNamespace(id=user.id)
    r = await task(session, user, "expired")
    await allowance_tasks.claim(session, user.id, "expired", "old-worker")
    r.lease_expires_at = allowance_tasks.now() - timedelta(seconds=1)
    session.add(r)
    await session.commit()
    with pytest.raises(HTTPException):
        await attempt(session, user, "expired", owner="old-worker")
    await session.rollback()
    for key in ("replacement", "parallel"):
        await task(session, user, key)
    r = await allowance.request_row(session, user.id, "expired")
    assert r.status == "failed" and r.charged == 0
    await allowance_tasks.claim(session, user.id, "replacement", "owner")
    with pytest.raises(HTTPException):
        await allowance_tasks.claim(
            session, user.id, "replacement", "reconnected-worker"
        )
    await session.rollback()
    await allowance.settle(
        session, user.id, "replacement", success=False, release_unknown=True
    )
    with pytest.raises(HTTPException):
        await attempt(session, user, "replacement", owner="owner")
    await session.rollback()
    await task(session, user, "after-cancel")


@pytest.mark.asyncio
async def test_unknown_and_refunded_child_costs_bound_subsequent_work(db):
    _, session, user = db
    user = SimpleNamespace(id=user.id)
    for key, known in (("unknown", False), ("refunded", True)):
        await task(session, user, key, supplier=1_000_000)
        await allowance_tasks.claim(session, user.id, key, "worker")
        aid = await attempt(session, user, key, budget=800_000)
        if known:
            await allowance.finish_attempt(session, aid, units=800_000, usage={})
        await allowance.settle(
            session, user.id, key, success=False, release_unknown=True
        )
    a = await allowance.account(session, user.id)
    assert a.spent == a.reserved == a.luna_reserved == 0
    supplier, loss = await allowance_tasks.exposure(session, user_id=user.id, days=30)
    assert supplier == loss == 1_600_000
    await session.commit()
    await task(session, user, "bounded", supplier=1_000_000)
    await allowance_tasks.claim(session, user.id, "bounded", "worker")
    with pytest.raises(HTTPException) as exc:
        await attempt(session, user, "bounded", budget=800_000)
    assert exc.value.detail["error"] == "user_supplier_spend_paused"
    await session.rollback()
    assert len((await session.exec(select(ProviderAttempt))).all()) == 2


@pytest.mark.asyncio
async def test_reset_cannot_charge_a_future_period_and_rollback_keeps_saved_policy(
    db, monkeypatch
):
    _, session, user = db
    user = SimpleNamespace(id=user.id)
    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_GENERATION_V2_ENABLED", True)
    r = await task(session, user, "reset")
    await allowance_tasks.claim(session, user.id, "reset", "worker")
    aid = await attempt(session, user, "reset")
    await allowance.finish_attempt(session, aid, units=1000, usage={})
    a = await allowance.account(session, user.id)
    a.period_start += timedelta(days=1)
    session.add(a)
    await session.commit()
    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_GENERATION_V2_ENABLED", False)
    await allowance.settle(session, user.id, "reset", success=True)
    assert r.charged == a.spent == a.reserved == 0
    old = await allowance.reserve(
        session,
        user_id=user.id,
        conversation_id=None,
        request_id="legacy",
        model="gpt-5.6-terra",
        ceiling=1000,
    )
    a = await allowance.account(session, user.id)
    assert old.admission_policy == "held-legacy" and a.reserved == 1000
    await allowance.settle(session, user.id, "legacy", success=False)
    assert a.reserved == 0


@pytest.mark.asyncio
async def test_recovery_once_known_only_and_failed_cost_is_internal(db):
    _, session, user = db
    user = SimpleNamespace(id=user.id)
    await task(session, user, "recover")
    await allowance_tasks.claim(session, user.id, "recover", "worker")
    aid = await attempt(session, user, "recover")
    with pytest.raises(HTTPException):
        await attempt(session, user, "recover", step="2", recovery=True)
    await session.rollback()
    await allowance.finish_attempt(
        session,
        aid,
        units=5000,
        usage={"incomplete_reason": "max_output_tokens"},
        success=False,
    )
    recovered = await attempt(session, user, "recover", step="2", recovery=True)
    await allowance.finish_attempt(session, recovered, units=1000, usage={})
    with pytest.raises(HTTPException):
        await attempt(session, user, "recover", step="3", recovery=True)
    await session.rollback()
    await allowance.settle(session, user.id, "recover", success=True)
    r = await allowance.request_row(session, user.id, "recover")
    assert r.charged == 1000
    assert await allowance_tasks.exposure(session, user_id=user.id, days=30) == (
        6000,
        5000,
    )


@pytest.mark.asyncio
async def test_parallel_batch_rollback_is_atomic_and_image_limit_is_durable(
    db, monkeypatch
):
    engine, session, user = db
    from app.services import shared_chat_provider as provider, shared_chat_loop as loop

    monkeypatch.setattr(provider, "engine", engine)
    monkeypatch.setattr(loop, "engine", engine)
    row = await task(session, user, "batch", supplier=4000)
    row.execution_plan = {**row.execution_plan, "image_consent": True}
    await session.commit()
    run = provider.ChatRun(user.id, "batch")
    await run.load_plan()
    calls = [{"name": "file_search", "args": {"query": q}} for q in ("one", "two")]
    with pytest.raises(HTTPException):
        await loop.prefunded_batch(
            run, calls, {"file_search": {"vector_store_ids": ["store"]}}, 0
        )
    assert (await session.exec(select(ProviderAttempt))).all() == []
    await session.refresh(row)
    assert row.execution_state["tools"] == 0
    row.supplier_ceiling = 500_000
    session.add(row)
    await session.commit()
    image = [
        {
            "name": "image_generation",
            "args": {"query": "Draw", "reference_mode": "none"},
        }
    ]
    children = await loop.prefunded_batch(
        run, image, {"image_generation": {"quality": "low"}}, 0
    )
    await run.finish(children[0].prefunded[0], "gpt-image-2.5-flare", {}, units=100)
    with pytest.raises(HTTPException) as exc:
        await loop.prefunded_batch(
            run, image, {"image_generation": {"quality": "low"}}, 0
        )
    assert exc.value.detail["error"] == "image_consent_or_limit"


@pytest.mark.asyncio
async def test_stable_consent_survives_small_balance_change_but_not_changed_task(
    estimate_case, monkeypatch
):
    from app.services import allowance_chat
    from app.api import chat_helpers
    from unittest.mock import AsyncMock

    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_GENERATION_V2_ENABLED", True)
    session, user, conversation, request = estimate_case
    first = await allowance_chat.estimate(session, user, conversation, request)
    request.estimate_reference = first["estimate_reference"]
    request.spend_limit_units = first["ceiling_units"]
    allowance_chat.allowance.account.return_value.spent += 500
    second = await allowance_chat.estimate(session, user, conversation, request)
    assert second["ceiling_units"] <= first["ceiling_units"]
    monkeypatch.setattr(
        chat_helpers,
        "_build_history_for_openai",
        AsyncMock(
            return_value=[
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Changed history"}],
                }
            ]
        ),
    )
    with pytest.raises(HTTPException) as exc:
        await allowance_chat.estimate(session, user, conversation, request)
    assert exc.value.detail["error"] == "usage_estimate_stale"


@pytest.mark.asyncio
@pytest.mark.parametrize("cap", [0, 1, 9999])
async def test_tiny_rich_user_caps_reject_without_supplier_spend(
    estimate_case, monkeypatch, cap
):
    from app.services import allowance_chat
    from unittest.mock import AsyncMock

    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_GENERATION_V2_ENABLED", True)
    session, user, conversation, request = estimate_case
    request.required_tool, request.tool_choice = None, []
    request.spend_limit_units = cap
    reserve = AsyncMock()
    monkeypatch.setattr(allowance, "reserve", reserve)
    with pytest.raises(HTTPException) as exc:
        await allowance_chat.admit(session, user, conversation, request)
    assert exc.value.status_code == 402
    reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_zero_paid_luna_omits_optional_paid_tools_and_rejects_required_tool(
    estimate_case, monkeypatch
):
    from app.services import allowance_chat

    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_GENERATION_V2_ENABLED", True)
    session, user, conversation, request = estimate_case
    account = allowance_chat.allowance.account.return_value
    account.spent = account.granted
    request.model = "gpt-5.6-luna"
    request.required_tool, request.tool_choice = None, "auto"
    quote = await allowance_chat.estimate(session, user, conversation, request)
    assert quote["ceiling_units"] == quote["minimum_ceiling_units"] == 0
    assert not quote["needs_confirmation"] and quote["execution_plan"]["tools"] == []
    request.required_tool = "web_search"
    with pytest.raises(HTTPException) as exc:
        await allowance_chat.estimate(session, user, conversation, request)
    assert exc.value.detail["error"] == "paid_tool_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model",
    [
        "gpt-5.6-luna",
        "gpt-5.6-terra",
        "gpt-5.6-sol",
        "gpt-6-astra",
        "claude-sonnet-5",
        "claude-opus-5",
        "claude-fable-5-1",
    ],
)
async def test_default_floor_funds_normal_first_answer_for_all_trial_models(
    estimate_case, monkeypatch, model
):
    from app.services import allowance_chat
    from app.services.allowance_policy import step_budget
    from app.services.shared_chat_provider import shared_instructions

    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_GENERATION_V2_ENABLED", True)
    session, user, conversation, request = estimate_case
    account = allowance_chat.allowance.account.return_value
    account.plan, account.granted, account.luna_granted = "starter", 750_000, 100_000
    account.trial_started_at = None
    request.model, request.reasoning_effort = model, None
    request.required_tool, request.tool_choice = None, []
    quote = await allowance_chat.estimate(session, user, conversation, request)
    cost = step_budget(
        model,
        [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": request.content[0].value}],
            }
        ],
        shared_instructions(model, "Be helpful."),
        max_output=quote["max_output_tokens"],
    )
    saved = quote["execution_plan"]
    assert cost <= saved["supplier_ceiling"]
    assert cost <= saved["risk_policy"]["user_loss_units"] <= 5_000_000


@pytest.mark.asyncio
async def test_original_account_is_used_after_scope_or_access_transition(
    db, monkeypatch
):
    _, session, user = db
    uid = user.id
    old = await allowance.account(session, uid)
    old.spent = old.granted - 1
    old_id = old.id
    await session.commit()
    await task(session, user, "transition", cap=1)
    await allowance_tasks.claim(session, uid, "transition", "worker")
    aid = await attempt(session, user, "transition")
    await allowance.finish_attempt(session, aid, units=100, usage={})
    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_SCOPE", "upgraded-scope")
    new = await allowance.account(session, uid)
    assert new.id != old_id
    await session.commit()
    await allowance.settle(session, uid, "transition", success=True)
    assert old.spent == old.granted and new.spent == 0
    assert (
        await allowance.request_row(session, uid, "transition")
    ).account_id == old_id


@pytest.mark.asyncio
async def test_prior_window_charges_cannot_hide_new_window_failures(db):
    _, session, user = db
    uid = user.id
    await task(session, user, "windows")
    await allowance_tasks.claim(session, uid, "windows", "worker")
    first = await attempt(session, user, "windows")
    await allowance.finish_attempt(session, first, units=800, usage={})
    p = await session.get(ProviderAttempt, first)
    p.created_at = allowance_tasks.now().replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    ) - timedelta(seconds=1)
    await session.commit()
    failed = await attempt(session, user, "windows", step="2")
    await allowance.finish_attempt(
        session, failed, units=100, usage={"stop_reason": "max_tokens"}, success=False
    )
    recovered = await attempt(session, user, "windows", step="3", recovery=True)
    await allowance.finish_attempt(session, recovered, units=200, usage={})
    await allowance.settle(session, uid, "windows", success=True)
    assert await allowance_tasks.exposure(session) == (300, 100)


@pytest.mark.asyncio
async def test_flag_rollback_cannot_bypass_live_task_slots(db, monkeypatch):
    _, session, user = db
    for key in ("first", "second"):
        await task(session, user, key)
    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_GENERATION_V2_ENABLED", False)
    with pytest.raises(HTTPException) as exc:
        await allowance.reserve(
            session,
            user_id=user.id,
            conversation_id=None,
            request_id="legacy-third",
            model="gpt-5.6-terra",
            ceiling=1000,
        )
    assert exc.value.detail["error"] == "active_task_limit"


@pytest.mark.asyncio
async def test_zero_balance_is_rechecked_atomically_before_luna_paid_tool_work(db):
    _, session, user = db
    a = await allowance.account(session, user.id)
    policy = limits(a)
    saved = dict(
        policy=ITERATIVE,
        generation=execution_plan("gpt-5.6-luna", []).dump(),
        tools=["web_search"],
        required_tool="web_search",
        image_consent=False,
        supplier_ceiling=500_000,
        risk_policy=policy,
        final_answer_units=10_000,
        recovery_tokens=16000,
    )
    a.spent = a.granted
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await allowance.reserve(
            session,
            user_id=user.id,
            conversation_id=None,
            request_id="required",
            model="gpt-5.6-luna",
            ceiling=1000,
            luna_ceiling=1000,
            execution_plan=saved,
        )
    assert exc.value.detail["error"] == "paid_tool_unavailable"
    await session.rollback()
    await session.refresh(user)
    optional = await allowance.reserve(
        session,
        user_id=user.id,
        conversation_id=None,
        request_id="optional",
        model="gpt-5.6-luna",
        ceiling=1000,
        luna_ceiling=1000,
        execution_plan={**saved, "required_tool": None},
    )
    assert optional.ceiling == 0 and optional.execution_plan["tools"] == []


@pytest.mark.asyncio
async def test_legacy_work_cannot_spend_saved_no_hold_final_protection(db, monkeypatch):
    _, session, user = db
    uid = user.id
    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_PROVIDER_BUDGET_UNITS", 1_000_000)
    await allowance.reserve(
        session,
        user_id=uid,
        conversation_id=None,
        request_id="old",
        model="gpt-5.6-terra",
        ceiling=300_000,
    )
    await task(session, user, "new")
    await allowance_tasks.claim(session, uid, "new", "worker")
    await allowance.begin_attempt(
        session,
        user_id=uid,
        request_id="new",
        owner="worker",
        step_key="1",
        model="gpt-5.6-terra",
        budget=50_000,
        protected=100_000,
    )
    with pytest.raises(HTTPException) as exc:
        await allowance.begin_attempt(
            session,
            user_id=uid,
            request_id="old",
            step_key="1",
            model="gpt-5.6-terra",
            budget=100_000,
        )
    assert exc.value.detail["error"] == "provider_failure_spend_paused"


@pytest.mark.asyncio
async def test_user_loss_reset_preserves_global_costs_and_new_failures(db, monkeypatch):
    _, session, original = db
    user = SimpleNamespace(id=original.id)
    for key, known in (("old-unknown", False), ("old-known", True)):
        await task(session, user, key, supplier=1_000_000)
        await allowance_tasks.claim(session, user.id, key, "worker")
        aid = await attempt(session, user, key, budget=800_000)
        if known:
            await allowance.finish_attempt(session, aid, units=800_000, usage={})
        await allowance.settle(
            session, user.id, key, success=False, release_unknown=True
        )
    before = await allowance_tasks.exposure(session)
    a = await allowance.account(session, user.id)
    balance = (a.granted, a.spent, a.reserved, a.luna_spent, a.luna_reserved)
    await task(session, user, "new-failure", supplier=1_000_000)
    await allowance_tasks.claim(session, user.id, "new-failure", "worker")
    with pytest.raises(HTTPException) as exc:
        await attempt(session, user, "new-failure", budget=800_000)
    assert exc.value.detail == {"error": "user_supplier_spend_paused"}
    await session.rollback()
    assert (
        await allowance_tasks.reset_user_loss_guard(session, user.id, "reset-one") == 2
    )
    await session.commit()
    assert await allowance_tasks.exposure(session) == before
    assert await allowance_tasks.exposure(session, user_id=user.id, days=30) == (
        1_600_000,
        0,
    )
    assert (a.granted, a.spent, a.reserved, a.luna_spent, a.luna_reserved) == balance
    with monkeypatch.context() as context:
        context.setattr(settings, "SHARED_ALLOWANCE_PROVIDER_BUDGET_UNITS", 1_700_000)
        with pytest.raises(HTTPException) as exc:
            await attempt(session, user, "new-failure", budget=800_000)
        assert exc.value.detail == {"error": "beta_spend_paused"}
        await session.rollback()
    aid = await attempt(session, user, "new-failure", budget=800_000)
    await allowance.finish_attempt(session, aid, units=800_000, usage={})
    await allowance.settle(session, user.id, "new-failure", success=False)
    assert await allowance_tasks.exposure(session, user_id=user.id, days=30) == (
        2_400_000,
        800_000,
    )
    # Retrying the same reset never waives failures that occurred after it.
    assert (
        await allowance_tasks.reset_user_loss_guard(session, user.id, "reset-one") == 0
    )
    await session.commit()
    assert await allowance_tasks.exposure(session, user_id=user.id, days=30) == (
        2_400_000,
        800_000,
    )
    events = (
        await session.exec(
            select(AllowanceEvent).where(
                AllowanceEvent.kind.in_(["user_loss_reset", "user_loss_waived"])
            )
        )
    ).all()
    assert len(events) == 3
    assert all(e.units == e.luna_units == 0 for e in events)
    attempts = (await session.exec(select(ProviderAttempt))).all()
    assert sum(p.supplier_units is None for p in attempts) == 1


@pytest.mark.asyncio
async def test_user_loss_reset_is_scoped_and_does_not_waive_active_work(db):
    from app.db.models import AppUser

    _, session, original = db
    user = SimpleNamespace(id=original.id)
    other = AppUser(default_prompt="Another test account")
    session.add(other)
    await session.commit()
    for who, key in ((user, "active"), (other, "other-failed")):
        await task(session, who, key)
        await allowance_tasks.claim(session, who.id, key, "worker")
        aid = await attempt(session, who, key)
        await allowance.finish_attempt(session, aid, units=5000, usage={})
        if who.id == other.id:
            await allowance.settle(session, who.id, key, success=False)
    other_before = await allowance_tasks.exposure(session, user_id=other.id, days=30)
    assert await allowance_tasks.reset_user_loss_guard(session, user.id, "scoped") == 0
    await session.commit()
    await allowance.settle(session, user.id, "active", success=False)
    assert await allowance_tasks.exposure(session, user_id=user.id, days=30) == (
        5000,
        5000,
    )
    assert (
        await allowance_tasks.exposure(session, user_id=other.id, days=30)
        == other_before
    )
    with pytest.raises(ValueError, match="different reset"):
        await allowance_tasks.reset_user_loss_guard(session, other.id, "scoped")
    await session.rollback()


@pytest.mark.asyncio
async def test_user_loss_reset_rollback_is_atomic(db):
    _, session, original = db
    user = SimpleNamespace(id=original.id)
    await task(session, user, "failed")
    await allowance_tasks.claim(session, user.id, "failed", "worker")
    aid = await attempt(session, user, "failed")
    await allowance.finish_attempt(session, aid, units=5000, usage={})
    await allowance.settle(session, user.id, "failed", success=False)
    before = await allowance_tasks.exposure(session, user_id=user.id, days=30)
    assert (
        await allowance_tasks.reset_user_loss_guard(session, user.id, "rollback") == 1
    )
    await session.rollback()
    assert await allowance_tasks.exposure(session, user_id=user.id, days=30) == before
    assert (
        await allowance_tasks.reset_user_loss_guard(session, user.id, "rollback") == 1
    )
    await session.commit()
