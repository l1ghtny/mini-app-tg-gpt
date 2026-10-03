import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import shared_chat_provider as provider
from app.services.generation_budget import execution_plan
from app.services.provider_errors import ProviderResponseError


def fake_run(model):
    plan = execution_plan(model, [])
    return SimpleNamespace(
        plan=plan,
        text_capacity=AsyncMock(return_value=plan.max_output_tokens),
        start=AsyncMock(return_value="attempt"),
        finish=AsyncMock(),
        identify=AsyncMock(),
        final_phase=False,
        recovering=False,
    )


def mock_claude(monkeypatch, *, truncated=False, empty=False):
    captured = []
    events = [
        {
            "type": "message_start",
            "message": {
                "id": "synthetic",
                "usage": {"input_tokens": 100, "output_tokens": 0},
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "tool_use",
                "id": "tool",
                "name": "web_search",
                "input": {},
            },
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {
                "type": "input_json_delta",
                "partial_json": '{"query":' if truncated else '{"query":"a"}',
            },
        },
        {
            "type": "message_delta",
            "delta": {"stop_reason": "max_tokens" if truncated else "tool_use"},
            "usage": {"output_tokens": 32000},
        },
        {"type": "message_stop"},
    ]

    if empty:
        events = [
            events[0],
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 32000},
            },
            events[-1],
        ]

    def respond(request):
        captured.append((json.loads(request.content), dict(request.headers)))
        return httpx.Response(
            200, text="\n\n".join("data: " + json.dumps(e) for e in events)
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        provider.httpx,
        "AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(respond)),
    )
    monkeypatch.setattr(provider.settings, "ANTHROPIC_API_KEY", "synthetic")
    return captured


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model", ["claude-opus-5", "claude-fable-5-1", "claude-sonnet-5"]
)
async def test_native_task_budget_and_final_tool_choice_preserve_prefix(
    monkeypatch, model
):
    captured = mock_claude(monkeypatch)
    run = fake_run(model)
    for phase in (False, True):
        run.final_phase = phase
        async for _ in provider.claude_turn(
            run,
            [],
            model,
            "Stable system",
            {"web_search": {}},
            None,
            run.plan.effort,
            0,
        ):
            pass
    first, second = [body for body, _ in captured]
    assert first["max_tokens"] == run.plan.max_output_tokens
    assert first["system"] == second["system"] and first["tools"] == second["tools"]
    assert second["tool_choice"] == {"type": "none"}
    if run.plan.task_budget:
        assert (
            first["output_config"]["task_budget"]
            == second["output_config"]["task_budget"]
            == {"type": "tokens", "total": run.plan.task_budget}
        )
        assert captured[0][1]["anthropic-beta"] == "task-budgets-2026-03-13"
    else:
        assert "task_budget" not in first["output_config"]
        assert "anthropic-beta" not in captured[0][1]


@pytest.mark.asyncio
async def test_truncated_tool_json_records_supplier_usage_without_executing(
    monkeypatch,
):
    mock_claude(monkeypatch, truncated=True)
    run = fake_run("claude-fable-5-1")
    with pytest.raises(ProviderResponseError) as error:
        async for _ in provider.claude_turn(
            run,
            [],
            run.plan.model,
            "System",
            {"web_search": {}},
            None,
            run.plan.effort,
            0,
        ):
            pass
    assert error.value.reason == "max_tokens" and error.value.has_tool_output
    run.finish.assert_awaited_once()
    assert run.finish.call_args.args[2]["output_tokens"] == 32000
    assert run.finish.call_args.kwargs["success"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [False, True])
async def test_openai_uses_planned_cap_effort_and_disables_recovery_tools(
    monkeypatch, empty
):
    class Stream:
        def __aiter__(self):
            async def events():
                yield SimpleNamespace(
                    type="response.completed",
                    response=SimpleNamespace(
                        id="synthetic",
                        status="completed",
                        output=[]
                        if empty
                        else [
                            SimpleNamespace(
                                model_dump=lambda **kw: {
                                    "type": "message",
                                    "content": [
                                        {"type": "output_text", "text": "Answer"}
                                    ],
                                }
                            )
                        ],
                        usage={"input_tokens": 100, "output_tokens": 30},
                    ),
                )

            return events()

        async def close(self):
            pass

    create = AsyncMock(return_value=Stream())
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    client.with_options = lambda **kw: client
    monkeypatch.setattr(provider, "client", client)
    run = fake_run("gpt-5.6-sol")
    run.recovering = True

    async def consume():
        async for _ in provider.openai_turn(
            run,
            [],
            run.plan.model,
            "System",
            {"web_search": {}},
            None,
            run.plan.effort,
            0,
        ):
            pass

    if empty:
        with pytest.raises(ProviderResponseError, match="empty_answer"):
            await consume()
        assert run.finish.call_args.kwargs["success"] is False
        assert run.finish.call_args.args[2]["failure_reason"] == "empty_answer"
    else:
        await consume()
    payload = create.call_args.kwargs
    assert payload["max_output_tokens"] == 16000
    assert payload["reasoning"] == {"effort": "low"}
    assert payload["tool_choice"] == "none" and payload["tools"]


@pytest.mark.asyncio
async def test_completed_claude_without_visible_answer_is_not_customer_success(
    monkeypatch,
):
    mock_claude(monkeypatch, empty=True)
    run = fake_run("claude-fable-5-1")
    with pytest.raises(ProviderResponseError, match="empty_answer"):
        async for _ in provider.claude_turn(
            run, [], run.plan.model, "System", {}, None, run.plan.effort, 0
        ):
            pass
    assert run.finish.call_args.kwargs["success"] is False
    assert run.finish.call_args.args[2]["output_tokens"] == 32000


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", ["", "partial answer"])
async def test_recovery_failure_does_not_start_a_third_call(partial):
    calls = []

    async def exhausted(*args):
        calls.append(args)
        if False:
            yield
        raise ProviderResponseError(
            status="incomplete", reason="max_tokens", partial_text=partial
        )

    run = SimpleNamespace(plan=True, recovered=False)
    with pytest.raises(ProviderResponseError):
        async for _ in provider.funded_turn(
            exhausted, run, [], "claude-fable-5-1", "", {}, None, "medium", 0
        ):
            pass
    assert len(calls) == 2
    assert all(
        part.get("type") != "thinking"
        for message in calls[1][1]
        for part in message.get("content", [])
    )


def test_multi_batch_summary_quote_reserves_all_batches_and_retries(monkeypatch):
    from app.services import allowance_context as context

    monkeypatch.setattr(context.settings, "SHARED_ALLOWANCE_HISTORY_TOKENS", 1000)
    messages = [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "unusual history " * 25000}],
        },
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "Latest question"}],
        },
    ]
    older, _, _, _ = context.context_plan(messages)
    assert len(context.summary_chunks(older)) > 1
    assert context.summary_budget(messages) > 50000
    assert context.summary_budget(messages[-1:]) == 0


@pytest.mark.asyncio
async def test_saved_tool_plan_survives_flag_rollback_and_deduplicates_work(
    monkeypatch,
):
    monkeypatch.setattr(
        provider.settings, "SHARED_ALLOWANCE_GENERATION_V2_ENABLED", False
    )
    monkeypatch.setattr(provider, "compress_context", AsyncMock(return_value=[]))

    async def load(run):
        run.plan = execution_plan("claude-fable-5-1", [])
        run.execution = {"tools": ["web_search"], "tool_rounds": 1}

    monkeypatch.setattr(provider.ChatRun, "load_plan", load)
    phases, evidence, tool_calls = [], [], []

    async def turn(run, history, *args):
        phases.append(run.final_phase)
        if not run.final_phase:
            calls = [
                dict(id=f"call-{i}", name="web_search", args={"query": "same"})
                for i in range(2)
            ]
            output = [
                dict(
                    type="tool_use",
                    id=call["id"],
                    name=call["name"],
                    input=call["args"],
                )
                for call in calls
            ]
            yield {"type": "turn.result", "calls": calls, "output": output}
        else:
            evidence.extend(history[-1]["content"])
            yield {"type": "turn.result", "calls": [], "output": []}

    async def tool(*args):
        tool_calls.append(args)
        yield {"type": "tool.result", "result": "Retained evidence"}

    monkeypatch.setattr(provider, "claude_turn", turn)
    monkeypatch.setattr(provider, "run_tool", tool)
    events = [
        event
        async for event in provider.stream_shared_response(
            [],
            "claude-fable-5-1",
            user_id="synthetic-user",
            request_id="saved-request",
            tools=[{"type": "web_search"}],
        )
    ]
    assert phases == [False, True] and len(tool_calls) == 1
    assert len(evidence) == 2 and all(
        part["content"] == "Retained evidence" for part in evidence
    )
    assert events[-1] == {"type": "done"}


@pytest.mark.asyncio
@pytest.mark.parametrize("deleted", [False, True])
async def test_terminal_failure_flushes_residual_text_without_resurrecting_deleted_message(
    monkeypatch, deleted
):
    import uuid
    from app.api import helpers as pipeline
    from app.services import allowance

    cid, mid, uid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    session = SimpleNamespace(
        get=AsyncMock(return_value=None if deleted else object()),
        rollback=AsyncMock(),
        execute=AsyncMock(),
        commit=AsyncMock(),
    )

    class Context:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(pipeline, "AsyncSession", lambda *a, **kw: Context())
    for name in (
        "_publish_initial_activity",
        "_record_and_publish_activity",
        "_cleanup_partial_images",
        "_clear_active_stream_pointer",
    ):
        monkeypatch.setattr(pipeline, name, AsyncMock())
    monkeypatch.setattr(allowance, "enabled", lambda _: True)
    settle = AsyncMock()
    monkeypatch.setattr(allowance, "settle", settle)
    upsert = AsyncMock()
    monkeypatch.setattr(pipeline, "_upsert_text", upsert)

    async def source(*args, **kwargs):
        yield {"type": "text.delta", "index": 0, "text": "Residual partial text"}
        raise ProviderResponseError(status="incomplete", reason="max_output_tokens")

    monkeypatch.setattr(pipeline, "stream_normalized_ai_response", source)
    bus = SimpleNamespace(
        r=SimpleNamespace(get=AsyncMock(return_value=None)),
        publish=AsyncMock(),
        mark_done=AsyncMock(),
    )
    await pipeline.generate_and_publish(
        cid, mid, uid, [], bus, [], request_id="partial-test"
    )
    if deleted:
        upsert.assert_not_awaited()
    else:
        upsert.assert_awaited_once()
        assert upsert.call_args.args[:3] == (mid, 0, "Residual partial text")
    assert all(call.kwargs["success"] is False for call in settle.await_args_list)
    bus.mark_done.assert_awaited_once()
