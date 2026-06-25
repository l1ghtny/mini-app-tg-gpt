import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.services.ai_service as ai_service
import app.services.perplexity_service as perplexity_service
from app.api.chat_helpers import (
    _validate_or_align_image_model,
    _validate_text_provider_request_capabilities,
)
from app.schemas.chat import MessageContent, NewMessageRequest
from app.services.model_registry import get_text_model_provider, models_share_provider


def test_perplexity_models_use_openai_image_fallback():
    assert get_text_model_provider("sonar") == "perplexity"
    assert models_share_provider("sonar", "gpt-image-1.5") is True
    assert models_share_provider("sonar", "gemini-3.1-flash-image-preview") is False

    assert _validate_or_align_image_model(
        model="sonar",
        image_model="gemini-3.1-flash-image-preview",
        explicit_image_model=False,
    ) == "gpt-image-1.5"


def test_perplexity_rejects_unsupported_image_paths():
    image_request = NewMessageRequest(
        client_request_id=str(uuid.uuid4()),
        role="user",
        content=[MessageContent(type="image_url", value="https://example.com/cat.png")],
        model="sonar",
        tool_choice="auto",
    )
    with pytest.raises(HTTPException) as image_exc:
        _validate_text_provider_request_capabilities(image_request, model_provider="perplexity")
    assert image_exc.value.status_code == 400
    assert image_exc.value.detail["error"] == "vision_not_supported_for_provider"

    generation_request = NewMessageRequest(
        client_request_id=str(uuid.uuid4()),
        role="user",
        content=[MessageContent(type="text", value="draw a cat")],
        model="sonar",
        tool_choice="image_generation",
    )
    with pytest.raises(HTTPException) as generation_exc:
        _validate_text_provider_request_capabilities(generation_request, model_provider="perplexity")
    assert generation_exc.value.status_code == 409
    assert generation_exc.value.detail["error"] == "image_generation_not_supported_for_provider"


@pytest.mark.asyncio
async def test_ai_service_routes_sonar_to_perplexity(monkeypatch):
    captured: dict = {}

    async def _fake_perplexity_stream(messages, model, **kwargs):
        captured["messages"] = messages
        captured["model"] = model
        captured.update(kwargs)
        yield {"type": "done"}

    monkeypatch.setattr(ai_service, "stream_normalized_perplexity_response", _fake_perplexity_stream)

    events = []
    async for event in ai_service.stream_normalized_ai_response(
        [{"role": "user", "content": [{"type": "input_text", "text": "latest AI news"}]}],
        model="sonar",
        instructions="Be concise.",
        request_id="req-1",
    ):
        events.append(event)

    assert events == [{"type": "done"}]
    assert captured["model"] == "sonar"
    assert captured["request_id"] == "req-1"


@pytest.mark.asyncio
async def test_perplexity_stream_maps_chat_completion_chunks(monkeypatch):
    captured_kwargs: dict = {}
    logged_usage: dict = {}

    class _FakeStream:
        def __init__(self):
            self._chunks = [
                SimpleNamespace(
                    id="pplx-1",
                    citations=["https://example.com/source"],
                    usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20),
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="Hello"))],
                ),
                SimpleNamespace(
                    id="pplx-1",
                    citations=[],
                    usage=None,
                    choices=[SimpleNamespace(delta=SimpleNamespace(content=" world"))],
                ),
            ]
            self._idx = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._idx >= len(self._chunks):
                raise StopAsyncIteration
            chunk = self._chunks[self._idx]
            self._idx += 1
            return chunk

    class _FakeCompletions:
        async def create(self, **kwargs):
            captured_kwargs.update(kwargs)
            return _FakeStream()

    class _FakeChat:
        def __init__(self):
            self.completions = _FakeCompletions()

    class _FakeClient:
        def __init__(self):
            self.chat = _FakeChat()

    async def _fake_log_usage(**kwargs):
        logged_usage.update(kwargs)

    monkeypatch.setattr(perplexity_service.settings, "PERPLEXITY_API_KEY", "test-key")
    monkeypatch.setattr(perplexity_service.settings, "PERPLEXITY_SEARCH_CONTEXT_SIZE", "low")
    monkeypatch.setattr(perplexity_service, "_build_client", lambda: _FakeClient())
    monkeypatch.setattr(perplexity_service, "_log_perplexity_usage", _fake_log_usage)

    events = []
    async for event in perplexity_service.stream_normalized_perplexity_response(
        [{"role": "user", "content": [{"type": "input_text", "text": "latest AI news"}]}],
        "sonar",
        instructions="Be concise.",
        user_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        request_id="req-2",
    ):
        events.append(event)

    assert captured_kwargs["model"] == "sonar"
    assert captured_kwargs["stream"] is True
    assert captured_kwargs["extra_body"]["web_search_options"]["search_context_size"] == "low"
    assert any(event.get("type") == "response.meta" and event.get("provider") == "perplexity" for event in events)
    assert any(event.get("type") == "part.start" for event in events)
    assert [event["text"] for event in events if event.get("type") == "text.delta"] == [
        "Hello",
        " world",
        "\n\n**Sources:**\n[1] https://example.com/source",
    ]
    assert any(event.get("type") == "text.done" for event in events)
    assert any(event.get("type") == "done" for event in events)
    assert logged_usage["status"] == "success"
    assert logged_usage["input_tokens"] == 10
    assert logged_usage["output_tokens"] == 20
    assert logged_usage["web_search_calls"] == 1
