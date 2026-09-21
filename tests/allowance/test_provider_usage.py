import pytest
from app.services.shared_chat_provider import (
    normalize_claude_usage,
    normalize_openai_usage,
)
from app.services.allowance_policy import usage_units


def test_claude_cache_is_added_to_total_input_only_once():
    normalized = normalize_claude_usage(
        {
            "input_tokens": 100,
            "cache_read_input_tokens": 200,
            "cache_creation_input_tokens": 300,
            "output_tokens": 400,
        }
    )
    assert normalized["input_tokens"] == 600
    assert usage_units("claude-sonnet-5", normalized) == 4990


def test_openai_reasoning_is_a_subset_of_output():
    normalized = normalize_openai_usage(
        {
            "input_tokens": 1000,
            "input_tokens_details": {"cached_tokens": 800},
            "output_tokens": 100,
            "output_tokens_details": {"reasoning_tokens": 70},
        }
    )
    assert normalized["reasoning_tokens"] == 70
    assert usage_units("gpt-5.6-terra", normalized) == 1760


def test_missing_claude_usage_is_not_a_free_success():
    with pytest.raises(ValueError):
        normalize_claude_usage({"output_tokens": 20})


def test_missing_openai_usage_is_not_a_free_success():
    with pytest.raises(ValueError):
        normalize_openai_usage({"output_tokens": 20})


@pytest.mark.asyncio
async def test_tool_continuation_omits_null_sdk_fields_but_keeps_reasoning(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from openai.types.responses import ResponseReasoningItem, ResponseFunctionToolCall
    from app.services import shared_chat_provider as provider

    reasoning = ResponseReasoningItem(
        id="rs_test", type="reasoning", summary=[], encrypted_content="opaque-state"
    )
    call = ResponseFunctionToolCall(
        id="fc_test", type="function_call", call_id="call_test",
        name="web_search", arguments='{"query":"Python release"}', status="completed",
    )
    response = SimpleNamespace(
        id="resp_test", status="completed", output=[reasoning, call],
        usage={"input_tokens":100,"output_tokens":30},
    )

    class Stream:
        async def __aiter__(self):
            yield SimpleNamespace(type="response.completed", response=response)

        async def close(self):
            pass

    create = AsyncMock(return_value=Stream())
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    client.with_options = lambda **kwargs: client
    monkeypatch.setattr(provider, "client", client)
    run = SimpleNamespace(
        text_capacity=AsyncMock(return_value=512), start=AsyncMock(return_value="attempt"),
        finish=AsyncMock(), identify=AsyncMock(),
    )
    events = [event async for event in provider.openai_turn(
        run, [{"role":"user","content":"Search"}], "gpt-5.6-luna", "", {}, None, "low", 0
    )]
    history = events[-1]["output"]
    assert history[0] == {
        "id":"rs_test", "type":"reasoning", "summary":[], "encrypted_content":"opaque-state"
    }
    assert history[1]["call_id"] == "call_test"
    assert "caller" not in history[1]
    assert "namespace" not in history[1]
    history.append({"type":"function_call_output","call_id":"call_test","output":"Found"})
    _ = [event async for event in provider.openai_turn(
        run, history, "gpt-5.6-luna", "", {}, None, "low", 1
    )]
    assert create.call_args.kwargs["input"] == history
