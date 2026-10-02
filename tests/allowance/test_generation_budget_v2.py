from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlmodel import select

from app.core.config import settings
from app.db.allowance import AllowanceEvent, ProviderAttempt
from app.services import allowance, allowance_chat, shared_chat_provider as provider
from app.services.generation_budget import PROFILES, ExecutionPlan, execution_plan
from app.services.provider_errors import ProviderResponseError


@pytest.fixture
def v2(monkeypatch):
    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_GENERATION_V2_ENABLED", True)


def message(text):
    return [{"role": "user", "content": [{"type": "input_text", "text": text}]}]


@pytest.mark.parametrize("model,values", PROFILES.items())
def test_normal_profile_does_not_need_semantic_classification(model, values):
    plan = execution_plan(model, message("Help me with this"))
    assert (plan.max_output_tokens, plan.effort) == (values[0], values[2])
    assert plan.profile == "normal"


def test_catalog_capacity_follows_account_policy_after_flag_rollback(monkeypatch):
    from app.services.allowance_policy import GRANT_VERSION_V2, public_plans

    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_GENERATION_V2_ENABLED", False)
    assert public_plans()[0]["allowance_units"] == 1250000
    assert public_plans(version=GRANT_VERSION_V2)[0]["allowance_units"] == 1750000


@pytest.mark.parametrize(
    "text", ["Write a 3,000-word report", "Напиши отчет на 3000 слов", "Write 4 pages"]
)
def test_explicit_length_expands_output_without_increasing_reasoning(text):
    plan = execution_plan("gpt-5.6-sol", message(text))
    assert plan.max_output_tokens == 32_000 and plan.effort == "low"


@pytest.mark.parametrize("effort", ["none", "low", "medium", "high", "xhigh", "max"])
def test_explicit_reasoning_is_preserved(effort):
    plan = execution_plan("gpt-5.6-sol", message("Hi"), reasoning_effort=effort)
    assert plan.effort == effort
    if effort in {"xhigh", "max"}:
        assert plan.max_output_tokens >= 64_000


def test_native_budgets_only_on_supported_models_and_expand_with_context():
    assert execution_plan("claude-sonnet-5", message("Hi")).task_budget is None
    for model in ("claude-opus-5", "claude-fable-5-1"):
        normal = execution_plan(model, message("Hi"))
        large = execution_plan(model, message("context " * 12000))
        assert normal.task_budget >= 20_000
        assert large.task_budget > normal.task_budget
        assert ExecutionPlan.load(normal.dump()) == normal


@pytest.mark.parametrize("model", ["gpt-6-astra", "claude-fable-5-1"])
def test_no_unsupported_none_reasoning(model):
    with pytest.raises(ValueError):
        execution_plan(model, message("Hi"), thinking_enabled=False)


@pytest.mark.asyncio
async def test_quote_and_admission_persist_identical_plan(
    estimate_case, monkeypatch, v2
):
    s, u, c, r = estimate_case
    r.required_tool = None
    r.tool_choice = []
    r.reasoning_effort = None
    r.client_request_id = "profile-admission"
    quote = await allowance_chat.estimate(s, u, c, r)
    assert quote["reasoning_effort"] == "low"
    assert quote["max_output_tokens"] == 12000
    assert quote["minimum_ceiling_units"] == 10_000
    assert not quote["needs_confirmation"]
    reserve = AsyncMock()
    monkeypatch.setattr(allowance, "reserve", reserve)
    r.estimate_reference = quote["estimate_reference"]
    await allowance_chat.admit(s, u, c, r)
    assert reserve.call_args.kwargs["execution_plan"] == quote["execution_plan"]
    assert reserve.call_args.kwargs["recovery_ceiling"] > 0


@pytest.mark.asyncio
async def test_small_spend_cap_fails_before_confirmation_or_reservation(
    estimate_case, monkeypatch, v2
):
    s, u, c, r = estimate_case
    r.required_tool = None
    r.tool_choice = []
    r.spend_limit_units = 9999
    reserve = AsyncMock()
    monkeypatch.setattr(allowance, "reserve", reserve)
    with pytest.raises(HTTPException) as exc:
        await allowance_chat.admit(s, u, c, r)
    assert exc.value.status_code == 402
    reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_signed_quote_expires_when_profile_or_spend_cap_changes(
    estimate_case, v2
):
    s, u, c, r = estimate_case
    r.required_tool = None
    r.tool_choice = []
    quote = await allowance_chat.estimate(s, u, c, r)
    r.estimate_reference = quote["estimate_reference"]
    r.response_length = "long"
    with pytest.raises(HTTPException) as exc:
        await allowance_chat.estimate(s, u, c, r)
    assert exc.value.detail["error"] == "usage_estimate_stale"


@pytest.mark.asyncio
async def test_grant_upgrade_keeps_spend_holds_and_is_idempotent(db, monkeypatch):
    _, session, user = db
    row = await allowance.account(session, user.id)
    row.spent, row.reserved = 100000, 50000
    await session.commit()
    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_GENERATION_V2_ENABLED", True)
    upgraded = await allowance.account(session, user.id)
    assert upgraded.granted == 1750000
    assert (upgraded.spent, upgraded.reserved, upgraded.luna_granted) == (
        100000,
        50000,
        290000,
    )
    await session.commit()
    await allowance.account(session, user.id)
    await session.commit()
    events = (
        await session.exec(
            select(AllowanceEvent).where(AllowanceEvent.kind == "plan_adjustment")
        )
    ).all()
    assert len(events) == 1 and events[0].units == 500000
    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_GENERATION_V2_ENABLED", False)
    from datetime import datetime

    future = await allowance.account(session, user.id, now=datetime(2027, 1, 1))
    assert future.granted == 1750000


@pytest.mark.asyncio
async def test_recovery_is_separate_funding_but_customer_never_exceeds_quote(
    db, monkeypatch, v2
):
    _, session, user = db
    uid = user.id
    row = await allowance.reserve(
        session,
        user_id=user.id,
        conversation_id=None,
        request_id="recovery",
        model="gpt-5.6-terra",
        ceiling=100,
        execution_plan={"version": "test"},
        recovery_ceiling=200,
    )
    first = await allowance.begin_attempt(
        session,
        user_id=user.id,
        request_id="recovery",
        step_key="1",
        model="gpt-5.6-terra",
        budget=100,
    )
    await allowance.finish_attempt(
        session,
        first,
        units=100,
        usage={"incomplete_reason": "max_output_tokens"},
        success=False,
    )
    recovery = await allowance.begin_attempt(
        session,
        user_id=user.id,
        request_id="recovery",
        step_key="2",
        model="gpt-5.6-terra",
        budget=200,
        recovery=True,
    )
    await allowance.finish_attempt(
        session, recovery, units=150, usage={"output_tokens": 3}
    )
    rid = row.id
    with pytest.raises(HTTPException):
        await allowance.begin_attempt(
            session,
            user_id=user.id,
            request_id="recovery",
            step_key="3",
            model="gpt-5.6-terra",
            budget=1,
            recovery=True,
        )
    await session.rollback()
    await allowance.settle(session, uid, "recovery", success=True)
    row = await session.get(type(row), rid)
    assert row.charged == 100
    attempts = (
        await session.exec(
            select(ProviderAttempt).where(ProviderAttempt.request_id == row.id)
        )
    ).all()
    assert sum(p.supplier_units for p in attempts) == 250


@pytest.mark.asyncio
async def test_unknown_attempt_cannot_be_replayed_as_recovery(db, v2):
    _, session, user = db
    await allowance.reserve(
        session,
        user_id=user.id,
        conversation_id=None,
        request_id="unknown-v2",
        model="gpt-5.6-terra",
        ceiling=100,
        execution_plan={},
        recovery_ceiling=200,
    )
    await allowance.begin_attempt(
        session,
        user_id=user.id,
        request_id="unknown-v2",
        step_key="1",
        model="gpt-5.6-terra",
        budget=100,
    )
    with pytest.raises(HTTPException) as exc:
        await allowance.begin_attempt(
            session,
            user_id=user.id,
            request_id="unknown-v2",
            step_key="2",
            model="gpt-5.6-terra",
            budget=200,
            recovery=True,
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_failure_exposure_pool_blocks_before_next_provider_call(
    db, monkeypatch, v2
):
    _, session, user = db
    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_PROVIDER_BUDGET_UNITS", 1000)
    await allowance.reserve(
        session,
        user_id=user.id,
        conversation_id=None,
        request_id="loss",
        model="gpt-5.6-terra",
        ceiling=300,
        execution_plan={"version": "test"},
    )
    with pytest.raises(HTTPException) as exc:
        await allowance.begin_attempt(
            session,
            user_id=user.id,
            request_id="loss",
            step_key="1",
            model="gpt-5.6-terra",
            budget=300,
        )
    assert exc.value.detail["error"] == "provider_failure_spend_paused"
    assert (await session.exec(select(ProviderAttempt))).all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason,tool_output", [("network", False), ("max_output_tokens", True)]
)
async def test_unsafe_failures_do_not_recover(reason, tool_output):
    calls = []

    async def failed(*args):
        calls.append(args)
        raise ProviderResponseError(
            status="incomplete", reason=reason, has_tool_output=tool_output
        )
        yield

    run = SimpleNamespace(plan=True, recovered=False)
    with pytest.raises(ProviderResponseError):
        async for _ in provider.funded_turn(
            failed, run, [], "gpt-5.6-terra", "", {}, None, "low", 0
        ):
            pass
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_known_cap_continues_partial_text_once_and_preserves_evidence():
    calls = []

    async def turn(*args):
        calls.append(args)
        if len(calls) == 1:
            yield {"type": "text.delta", "index": 0, "text": "First part."}
            raise ProviderResponseError(
                status="incomplete",
                reason="max_output_tokens",
                partial_text="First part.",
            )
        yield {"type": "text.delta", "index": 0, "text": " Second part."}
        yield {"type": "turn.result", "output": [], "calls": []}

    run = SimpleNamespace(plan=True, recovered=False)
    evidence = [
        {"role": "user", "content": [{"type": "input_text", "text": "Saved evidence"}]}
    ]
    events = [
        e
        async for e in provider.funded_turn(
            turn, run, evidence, "gpt-5.6-terra", "", {}, None, "low", 0
        )
    ]
    assert (
        "".join(e["text"] for e in events if e["type"] == "text.delta")
        == "First part. Second part."
    )
    assert calls[1][1][0] == evidence[0]
    assert run.recovered and not run.recovering


@pytest.mark.asyncio
async def test_two_concurrent_recoveries_have_one_winner(db, v2):
    import asyncio
    from sqlmodel.ext.asyncio.session import AsyncSession

    engine, session, user = db
    uid = user.id
    await allowance.reserve(
        session,
        user_id=uid,
        conversation_id=None,
        request_id="parallel-recovery",
        model="gpt-5.6-terra",
        ceiling=100,
        execution_plan={"version": "test"},
        recovery_ceiling=200,
    )
    first = await allowance.begin_attempt(
        session,
        user_id=uid,
        request_id="parallel-recovery",
        step_key="1",
        model="gpt-5.6-terra",
        budget=100,
    )
    await allowance.finish_attempt(
        session,
        first,
        units=100,
        usage={"incomplete_reason": "max_output_tokens"},
        success=False,
    )

    async def start(key):
        async with AsyncSession(engine, expire_on_commit=False) as concurrent:
            try:
                await allowance.begin_attempt(
                    concurrent,
                    user_id=uid,
                    request_id="parallel-recovery",
                    step_key=key,
                    model="gpt-5.6-terra",
                    budget=200,
                    recovery=True,
                )
                return "started"
            except HTTPException as exc:
                await concurrent.rollback()
                return exc.status_code

    results = await asyncio.gather(start("2"), start("3"))
    assert sorted(str(r) for r in results) == ["409", "started"]


@pytest.mark.asyncio
async def test_routing_cannot_use_protected_final_answer_funds(db, monkeypatch, v2):
    engine, session, user = db
    model = "gpt-5.6-terra"
    plan = execution_plan(
        model, message("Search"), reasoning_effort="low", tool_work=True
    )
    await allowance.reserve(
        session,
        user_id=user.id,
        conversation_id=None,
        request_id="protected",
        model=model,
        ceiling=500000,
        execution_plan={
            "generation": plan.dump(),
            "tools": ["web_search"],
            "final_answer_units": 400000,
            "tool_budget_units": 80000,
            "recovery_tokens": 24000,
        },
    )
    monkeypatch.setattr(provider, "engine", engine)
    run = provider.ChatRun(user.id, "protected")
    await run.load_plan()
    with pytest.raises(HTTPException):
        await run.text_capacity(
            model, message("Search"), "", "low", "web_search", {"web_search": {}}
        )
    assert (await session.exec(select(ProviderAttempt))).all() == []


@pytest.mark.asyncio
async def test_trial_upgrade_never_restarts_lifetime_clock(db, monkeypatch):
    from datetime import UTC, datetime, timedelta

    _, session, user = db
    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_TRIAL_ENABLED", True)
    trial = await allowance.account(session, user.id)
    start = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=2)
    end = start + timedelta(days=7)
    trial.trial_started_at, trial.period_end, trial.spent = start, end, 200000
    await session.commit()
    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_GENERATION_V2_ENABLED", True)
    upgraded = await allowance.account(session, user.id)
    assert upgraded.id == trial.id and upgraded.granted == 750000
    assert (upgraded.trial_started_at, upgraded.period_end, upgraded.spent) == (
        start,
        end,
        200000,
    )
