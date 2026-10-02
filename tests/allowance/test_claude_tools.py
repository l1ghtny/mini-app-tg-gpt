import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import shared_chat_provider as provider


@pytest.mark.asyncio
async def test_fable_required_tool_uses_auto_and_preserves_call(monkeypatch):
    captured = {}
    events = [
        {
            "type": "message_start",
            "message": {
                "id": "test",
                "usage": {"input_tokens": 10, "output_tokens": 0},
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "tool_use",
                "id": "call",
                "name": "web_search",
                "input": {},
            },
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {
                "type": "input_json_delta",
                "partial_json": '{"query":"Python version"}',
            },
        },
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use"},
            "usage": {"output_tokens": 20},
        },
        {"type": "message_stop"},
    ]

    def respond(request):
        captured.update(json.loads(request.content))
        return httpx.Response(
            200, text="\n\n".join("data: " + json.dumps(e) for e in events)
        )

    client = httpx.AsyncClient
    monkeypatch.setattr(
        provider.httpx,
        "AsyncClient",
        lambda **kw: client(transport=httpx.MockTransport(respond)),
    )
    monkeypatch.setattr(provider.settings, "ANTHROPIC_API_KEY", "synthetic")
    run = SimpleNamespace(
        text_capacity=AsyncMock(return_value=1024),
        start=AsyncMock(return_value="attempt"),
        finish=AsyncMock(),
        identify=AsyncMock(),
    )
    result = [
        event
        async for event in provider.claude_turn(
            run,
            [],
            "claude-fable-5-1",
            "Assistant",
            {"web_search": {}, "image_generation": {}},
            "web_search",
            "low",
            0,
        )
    ]
    assert captured["tool_choice"] == {"type": "auto"}
    assert captured["cache_control"] == {"type": "ephemeral"}
    assert [tool["name"] for tool in captured["tools"]] == ["web_search"]
    assert captured["system"][0]["text"] == "Assistant"
    assert result[-1]["calls"][0]["name"] == "web_search"
    assert run.finish.call_args.kwargs["success"] is True


@pytest.mark.asyncio
async def test_claude_tool_followup_keeps_signed_prefix(monkeypatch):
    monkeypatch.setattr(provider, "compress_context", AsyncMock(return_value=[]))
    calls = []

    async def fake_claude_turn(
        run, messages, model, instructions, tools, required, effort, index
    ):
        calls.append((instructions, list(tools), required, list(messages)))
        if len(calls) == 1:
            yield {
                "type": "turn.result",
                "output": [
                    {"type": "thinking", "thinking": "", "signature": "signed"},
                    {
                        "type": "tool_use",
                        "id": "search-1",
                        "name": "web_search",
                        "input": {"query": "a"},
                    },
                    {
                        "type": "tool_use",
                        "id": "search-2",
                        "name": "web_search",
                        "input": {"query": "b"},
                    },
                ],
                "calls": [
                    {"id": "search-1", "name": "web_search", "args": {"query": "a"}},
                    {"id": "search-2", "name": "web_search", "args": {"query": "b"}},
                ],
            }
        else:
            yield {"type": "turn.result", "output": [], "calls": []}

    async def fake_run_tool(*args):
        yield {"type": "tool.result", "result": "Synthetic search result"}

    monkeypatch.setattr(provider, "claude_turn", fake_claude_turn)
    monkeypatch.setattr(provider, "run_tool", fake_run_tool)
    events = [
        event
        async for event in provider.stream_shared_response(
            [],
            "claude-fable-5-1",
            tools=[{"type": "web_search"}, {"type": "image_generation"}],
            tool_choice={"type": "web_search"},
        )
    ]
    assert events[-1] == {"type": "done"}
    assert len(calls) == 2
    assert calls[0][0] == calls[1][0]
    assert calls[0][1] == calls[1][1] == ["web_search"]
    assert "Call this tool before" in calls[0][0]
    assert [part["type"] for part in calls[1][3][-2]["content"]] == [
        "thinking",
        "tool_use",
        "tool_use",
    ]
    assert [part["type"] for part in calls[1][3][-1]["content"]] == [
        "tool_result",
        "tool_result",
    ]


@pytest.mark.asyncio
async def test_required_tool_cannot_silently_be_skipped(monkeypatch):
    monkeypatch.setattr(provider, "compress_context", AsyncMock(return_value=[]))

    async def skipped(*args):
        yield {"type": "turn.result", "output": [], "calls": []}

    monkeypatch.setattr(provider, "claude_turn", skipped)
    with pytest.raises(RuntimeError, match="did not use the requested tool"):
        async for _ in provider.stream_shared_response(
            [],
            "claude-fable-5-1",
            tools=[{"type": "web_search"}],
            tool_choice={"type": "web_search"},
        ):
            pass
