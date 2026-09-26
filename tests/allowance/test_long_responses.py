from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app.services import allowance_chat, generation_observability as telemetry
from app.services.allowance_policy import MODELS, output_target
from app.services.provider_errors import ProviderResponseError


@pytest.mark.parametrize("model", list(MODELS))
@pytest.mark.parametrize("effort", ["none", "low", "medium", "high"])
def test_long_answer_capacity_is_independent_of_reasoning(model, effort):
    assert output_target(model, effort) == 16384
    assert output_target(model, effort, "file_search") == 2048


@pytest.mark.asyncio
async def test_long_answer_quote_confirms_real_capacity_before_spend(
    estimate_case, monkeypatch
):
    s, u, c, r = estimate_case
    r.model = "gpt-6-astra"
    r.tool_choice = []
    r.required_tool = None
    account = await allowance_chat.allowance.account(s, u.id)
    account.plan = "premium"
    account.granted = 6250000
    quote = await allowance_chat.estimate(s, u, c, r)
    assert quote["ceiling_units"] > 16384 * 50
    assert quote["needs_confirmation"]
    reserve = AsyncMock()
    monkeypatch.setattr(allowance_chat.allowance, "reserve", reserve)
    with pytest.raises(HTTPException) as error:
        await allowance_chat.admit(s, u, c, r)
    assert error.value.detail["error"] == "usage_confirmation_required"
    reserve.assert_not_called()
    r.spend_limit_units = 100000
    capped = await allowance_chat.estimate(s, u, c, r)
    assert capped["ceiling_units"] == 100000


@pytest.mark.asyncio
async def test_intermediate_tool_turn_never_marks_shared_request_consumed(monkeypatch):
    from app.api import helpers

    finalize = AsyncMock()
    monkeypatch.setattr(helpers, "finalize_request", finalize)
    args = dict(
        assistant_message_id="message",
        session=SimpleNamespace(),
        request_id="request",
        user_id="user",
        conversation_id="chat",
        tools=[],
        bus=None,
        image_entitlement_tier_id=None,
        image_entitlement_pack_id=None,
        buffers={},
        last_ckpt={},
        content_cache={},
        partial_image_keys={},
        lifecycle={"shared_allowance": True},
        chain_context_fingerprint=None,
    )
    await helpers._handle_stream_event(ev={"type": "text.done", "index": 0}, **args)
    finalize.assert_not_awaited()
    await helpers._handle_stream_event(
        ev={"type": "error", "code": "response_capacity_exceeded"}, **args
    )
    await helpers._handle_stream_event(ev={"type": "done"}, **args)
    finalize.assert_awaited_once_with(
        args["session"], request_id="request", user_id="user", success=False
    )


@pytest.mark.asyncio
async def test_shared_success_finalizes_only_once_at_terminal_done(monkeypatch):
    from app.api import helpers

    finalize = AsyncMock()
    monkeypatch.setattr(helpers, "finalize_request", finalize)
    args = dict(
        assistant_message_id="message",
        session=SimpleNamespace(),
        request_id="request",
        user_id="user",
        conversation_id="chat",
        tools=[],
        bus=None,
        image_entitlement_tier_id=None,
        image_entitlement_pack_id=None,
        buffers={},
        last_ckpt={},
        content_cache={},
        partial_image_keys={},
        lifecycle={"shared_allowance": True},
        chain_context_fingerprint=None,
    )
    for event in [
        {"type": "text.done", "index": 0},
        {"type": "done"},
        {"type": "done"},
    ]:
        await helpers._handle_stream_event(ev=event, **args)
    finalize.assert_awaited_once_with(
        args["session"], request_id="request", user_id="user", success=True
    )


@pytest.mark.parametrize(
    "exception,code",
    [
        (
            ProviderResponseError(status="incomplete", reason="max_output_tokens"),
            "response_capacity_exceeded",
        ),
        (
            ProviderResponseError(status="incomplete", reason="max_tokens"),
            "response_capacity_exceeded",
        ),
        (TimeoutError(), "generation_timeout"),
        (None, "response_capacity_exceeded"),
    ],
)
def test_handled_failures_are_reported_once_without_private_payloads(
    monkeypatch, exception, code
):
    scope = Mock()

    @contextmanager
    def new_scope():
        yield scope

    capture = Mock()
    monkeypatch.setattr(telemetry.sentry_sdk, "new_scope", new_scope)
    monkeypatch.setattr(telemetry.sentry_sdk, "capture_message", capture)
    lifecycle = {
        "last_error_code": "response_capacity_exceeded",
        "last_error_message": "PRIVATE",
    }
    for _ in range(2):
        telemetry.report_generation_failure(
            lifecycle, model="gpt-6-astra", request_id="test", exception=exception
        )
    capture.assert_called_once_with("Chat generation failed", level="error")
    scope.set_tag.assert_any_call("chat.error_code", code)
    assert "PRIVATE" not in str(scope.mock_calls)


def test_reporting_failure_cannot_break_customer_refund(monkeypatch):
    monkeypatch.setattr(
        telemetry.sentry_sdk, "new_scope", Mock(side_effect=RuntimeError("offline"))
    )
    telemetry.report_generation_failure({}, model="gpt-6-astra", request_id="test")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_mode", ["exception", "error_event", "missing_done", "cancelled"]
)
async def test_pipeline_reports_unexpected_failure_and_refunds(
    monkeypatch, failure_mode
):
    import uuid
    from app.api import helpers as pipeline
    from app.services import allowance
    from app.services.chat_cancellation import GenerationStopped

    session = SimpleNamespace(
        get=AsyncMock(return_value=None),
        execute=AsyncMock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
        add=Mock(),
    )

    class Session:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *args):
            pass

    class Events:
        async def __aenter__(self):
            return source()

        async def __aexit__(self, *args):
            pass

    async def source():
        yield {"type": "text.done", "index": 0}
        if failure_mode == "exception":
            raise ProviderResponseError(status="incomplete", reason="max_output_tokens")
        if failure_mode == "cancelled":
            raise GenerationStopped()
        if failure_mode == "error_event":
            yield {
                "type": "error",
                "code": "response_capacity_exceeded",
                "error": "safe failure",
            }
            yield {"type": "done"}

    monkeypatch.setattr(pipeline, "AsyncSession", lambda *a, **k: Session())
    monkeypatch.setattr(pipeline, "aclosing", lambda *a, **k: Events())
    monkeypatch.setattr(pipeline, "cancellable_events", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "stream_normalized_ai_response", lambda *a, **k: None)
    for name in (
        "_publish_initial_activity",
        "_record_and_publish_activity",
        "_cleanup_partial_images",
        "_clear_active_stream_pointer",
    ):
        monkeypatch.setattr(pipeline, name, AsyncMock())
    finalize = AsyncMock()
    monkeypatch.setattr(pipeline, "finalize_request", finalize)
    monkeypatch.setattr(allowance, "enabled", lambda _: True)
    settle = AsyncMock()
    monkeypatch.setattr(allowance, "settle", settle)
    report = Mock()
    monkeypatch.setattr(telemetry, "report_generation_failure", report)
    bus = SimpleNamespace(publish=AsyncMock(), mark_done=AsyncMock())
    await pipeline.generate_and_publish(
        uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), [], bus, [], request_id="regression"
    )
    assert settle.await_count >= 1
    assert all(call.kwargs["success"] is False for call in settle.call_args_list)
    assert not any(call.kwargs["success"] for call in finalize.call_args_list)
    if failure_mode == "cancelled":
        report.assert_not_called()
    else:
        report.assert_called_once()
        assert bus.mark_done.call_args.kwargs["ok"] is False
