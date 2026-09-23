"""Long-chat summary recovery, budget guards and settlement regressions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlmodel import select

from app.db.allowance import ProviderAttempt
from app.services import allowance, allowance_context as context
from app.services import shared_chat_provider as provider
from app.services.allowance_policy import LUNA
from app.services.provider_errors import ProviderResponseError


def response(status="completed", reason=None, output_text="Budget 42; no nuts."):
    return SimpleNamespace(
        id="resp_test",
        status=status,
        incomplete_details=SimpleNamespace(reason=reason) if reason else None,
        output=[],
        output_text=output_text,
        usage={"input_tokens": 8909, "output_tokens": 1536},
    )


def provider_client(monkeypatch, *results):
    create = AsyncMock(side_effect=results)
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    client.with_options = lambda **kw: client
    monkeypatch.setattr(provider, "client", client)
    return create


@pytest.mark.asyncio
async def test_incomplete_summary_retries_original_context_and_records_reason(
    monkeypatch,
):
    create = provider_client(
        monkeypatch, response("incomplete", "max_output_tokens"), response()
    )
    run = provider.ChatRun("user", "request")
    run.start = AsyncMock(side_effect=["attempt-1", "attempt-2"])
    run.finish = AsyncMock()
    result = await context.summarize_chunk(run, "Existing fact 42", "New fact: no nuts")
    assert result == "Budget 42; no nuts."
    calls = [call.kwargs for call in create.await_args_list]
    assert [c["max_output_tokens"] for c in calls] == [1536, 3072]
    assert calls[0]["input"] == calls[1]["input"]
    assert "600 tokens" in calls[1]["instructions"]
    assert "Budget 42; no nuts." not in str(calls[1]["input"])
    first, second = run.finish.await_args_list
    assert first.args[2]["incomplete_reason"] == "max_output_tokens"
    assert first.args[2]["response_status"] == "incomplete"
    assert first.kwargs == {"success": False, "units": 3625}
    assert second.kwargs == {"success": True, "units": 3625}


@pytest.mark.parametrize(
    "error",
    [
        ProviderResponseError(status="incomplete", reason="content_filter"),
        ProviderResponseError(status="failed", reason="unknown"),
        HTTPException(429, detail="quota"),
        TimeoutError("upstream timeout"),
    ],
)
@pytest.mark.asyncio
async def test_other_provider_failures_do_not_retry(error):
    run = SimpleNamespace(response=AsyncMock(side_effect=error))
    with pytest.raises(type(error)):
        await context.summarize_chunk(run, "old", "new")
    run.response.assert_awaited_once()


@pytest.mark.asyncio
async def test_recovery_stops_after_one_retry():
    run = SimpleNamespace(
        response=AsyncMock(
            side_effect=ProviderResponseError(
                status="incomplete", reason="max_output_tokens"
            )
        )
    )
    with pytest.raises(ProviderResponseError):
        await context.summarize_chunk(run, "old", "new")
    assert run.response.await_count == 2


@pytest.mark.parametrize("invalid", ["", " \n", "fact " * 1600])
@pytest.mark.asyncio
async def test_empty_or_oversized_summary_is_never_saved_or_silently_truncated(invalid):
    run = SimpleNamespace(
        response=AsyncMock(return_value=(response(output_text=invalid), []))
    )
    with pytest.raises(RuntimeError, match="History summary could not be completed"):
        await context.summarize_chunk(run, "old", "new")
    assert run.response.await_count == 2


@pytest.mark.asyncio
async def test_recovered_summary_is_saved_once_and_reused(monkeypatch):
    monkeypatch.setattr(context.settings, "SHARED_ALLOWANCE_HISTORY_TOKENS", 40)
    old_id = str(uuid4())
    conv = SimpleNamespace(
        id="chat",
        user_id="user",
        history_summary="",
        history_summary_up_to_message_id=None,
        updated_at=1,
    )
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.get.return_value = conv
    session.exec.return_value = SimpleNamespace(first=lambda: conv)
    session.add = lambda obj: None
    monkeypatch.setattr(context, "AsyncSession", lambda *a, **kw: session)
    run = SimpleNamespace(
        conversation_id="chat",
        user_id="user",
        response=AsyncMock(
            side_effect=[
                ProviderResponseError(status="incomplete", reason="max_output_tokens"),
                (response(), []),
            ]
        ),
    )
    messages = [
        {
            "role": "user",
            "_message_id": old_id,
            "content": [{"type": "input_text", "text": "Historical fact. " * 100}],
        },
        {
            "role": "user",
            "_message_id": str(uuid4()),
            "content": [{"type": "input_text", "text": "Current question"}],
        },
    ]
    result = await context.compress_context(run, messages)
    assert "Budget 42; no nuts." in str(result)
    assert str(conv.history_summary_up_to_message_id) == old_id
    session.commit.assert_awaited_once()
    await context.compress_context(run, messages)
    assert run.response.await_count == 2


@pytest.mark.asyncio
async def test_recovery_does_not_advance_summary_when_both_attempts_fail(monkeypatch):
    monkeypatch.setattr(context.settings, "SHARED_ALLOWANCE_HISTORY_TOKENS", 5)
    conv = SimpleNamespace(
        user_id="user",
        history_summary="Previous summary",
        history_summary_up_to_message_id=None,
        updated_at=1,
    )
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.get.return_value = conv
    monkeypatch.setattr(context, "AsyncSession", lambda *a, **kw: session)
    run = SimpleNamespace(
        conversation_id="chat",
        user_id="user",
        response=AsyncMock(
            side_effect=ProviderResponseError(
                status="incomplete", reason="max_output_tokens"
            )
        ),
    )
    with pytest.raises(ProviderResponseError):
        await context.compress_context(
            run,
            [
                {
                    "role": "user",
                    "_message_id": str(uuid4()),
                    "content": [{"type": "input_text", "text": "old " * 100}],
                },
                {
                    "role": "user",
                    "_message_id": str(uuid4()),
                    "content": [{"type": "input_text", "text": "new"}],
                },
            ],
        )
    assert conv.history_summary == "Previous summary"
    assert conv.history_summary_up_to_message_id is None
    session.commit.assert_not_awaited()


@pytest.mark.parametrize("success", [True, False])
@pytest.mark.asyncio
async def test_summary_recovery_settlement_and_supplier_cost(db, monkeypatch, success):
    engine, session, user = db
    monkeypatch.setattr(provider, "engine", engine)
    provider_client(
        monkeypatch, response("incomplete", "max_output_tokens"), response()
    )
    await allowance.reserve(
        session,
        user_id=user.id,
        conversation_id=None,
        request_id="recovery",
        model=LUNA,
        ceiling=10000,
        luna_ceiling=50000,
    )
    run = provider.ChatRun(user.id, "recovery")
    await context.summarize_chunk(run, "old", "fact " * 8700)
    await allowance.settle(session, user.id, "recovery", success=success)
    account = await allowance.account(session, user.id)
    await session.refresh(account)
    attempts = (await session.exec(select(ProviderAttempt))).all()
    assert sum(p.supplier_units for p in attempts) == 7250
    assert sorted(p.status for p in attempts) == ["complete", "failed"]
    assert account.spent == account.reserved == account.luna_reserved == 0
    assert account.luna_spent == (7250 if success else 0)


@pytest.mark.asyncio
async def test_retry_budget_rejected_before_second_provider_call(db, monkeypatch):
    engine, session, user = db
    monkeypatch.setattr(provider, "engine", engine)
    create = provider_client(monkeypatch, response("incomplete", "max_output_tokens"))
    await allowance.reserve(
        session,
        user_id=user.id,
        conversation_id=None,
        request_id="bounded",
        model=LUNA,
        ceiling=10000,
        luna_ceiling=8000,
    )
    with pytest.raises(HTTPException) as exc:
        await context.summarize_chunk(
            provider.ChatRun(user.id, "bounded"), "old", "fact " * 8700
        )
    assert exc.value.detail["error"] == "request_spend_limit"
    create.assert_awaited_once()
    await allowance.settle(session, user.id, "bounded", success=False)
    account = await allowance.account(session, user.id)
    await session.refresh(account)
    assert account.spent == account.luna_spent == account.luna_reserved == 0
