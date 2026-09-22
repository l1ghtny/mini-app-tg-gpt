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
    assert "Call this tool before" in captured["system"][0]["text"]
    assert result[-1]["calls"][0]["name"] == "web_search"
    assert run.finish.call_args.kwargs["success"] is True


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
