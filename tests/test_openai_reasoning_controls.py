import uuid
from types import SimpleNamespace

import pytest

import app.services.ai_service as ai_service
import app.services.openai_service as openai_service


@pytest.mark.asyncio
async def test_openai_thinking_false_disables_reasoning_summary(monkeypatch):
    captured: dict = {}

    async def _fake_openai_stream(_messages, _model, **kwargs):
        captured.update(kwargs)
        yield {"type": "done"}

    monkeypatch.setattr(ai_service, "stream_normalized_openai_response", _fake_openai_stream, raising=True)

    events = []
    async for ev in ai_service.stream_normalized_ai_response(
        [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        model="gpt-5.4-nano",
        thinking_enabled=False,
    ):
        events.append(ev)

    assert any(e.get("type") == "done" for e in events)
    assert captured.get("reasoning_summary") is None
    assert captured.get("reasoning_effort") is None


@pytest.mark.asyncio
async def test_openai_thinking_true_enables_reasoning_effort(monkeypatch):
    captured: dict = {}

    async def _fake_openai_stream(_messages, _model, **kwargs):
        captured.update(kwargs)
        yield {"type": "done"}

    monkeypatch.setattr(ai_service, "stream_normalized_openai_response", _fake_openai_stream, raising=True)

    events = []
    async for ev in ai_service.stream_normalized_ai_response(
        [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        model="gpt-5.4-nano",
        thinking_enabled=True,
    ):
        events.append(ev)

    assert any(e.get("type") == "done" for e in events)
    assert captured.get("reasoning_summary") == "concise"
    assert captured.get("reasoning_effort") == "medium"


@pytest.mark.asyncio
async def test_openai_reasoning_text_events_are_mapped(monkeypatch):
    class _FakeStream:
        def __init__(self):
            output_tokens_details = SimpleNamespace(reasoning_tokens=7)
            usage = SimpleNamespace(input_tokens=11, output_tokens=13, output_tokens_details=output_tokens_details)
            response = SimpleNamespace(id="resp_ok", usage=usage)
            self._events = [
                SimpleNamespace(
                    type="response.reasoning_text.delta",
                    delta="reasoning chunk",
                    output_index=0,
                    summary_index=0,
                    item_id="rs_1",
                    sequence_number=1,
                ),
                SimpleNamespace(
                    type="response.reasoning_text.done",
                    text="reasoning final",
                    output_index=0,
                    summary_index=0,
                    item_id="rs_1",
                    sequence_number=2,
                ),
                SimpleNamespace(type="response.completed", response=response, sequence_number=3),
            ]
            self._idx = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._idx >= len(self._events):
                raise StopAsyncIteration
            event = self._events[self._idx]
            self._idx += 1
            return event

    class _FakeResponses:
        async def create(self, **_kwargs):
            return _FakeStream()

    class _FakeClient:
        def __init__(self):
            self.responses = _FakeResponses()

    class _DummyAsyncSession:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def _noop_log_usage(*_args, **_kwargs):
        return None

    monkeypatch.setattr(openai_service, "client", _FakeClient(), raising=True)
    monkeypatch.setattr(openai_service, "AsyncSession", _DummyAsyncSession, raising=True)
    monkeypatch.setattr(openai_service, "log_usage", _noop_log_usage, raising=True)

    events = []
    async for ev in openai_service.stream_normalized_openai_response(
        [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        model="gpt-5.4-nano",
        user_id=uuid.uuid4(),
    ):
        events.append(ev)

    assert any(e.get("type") == "reasoning.summary.delta" for e in events)
    assert any(e.get("type") == "reasoning.summary.done" for e in events)
    assert any(e.get("type") == "usage" and e.get("reasoning_tokens") == 7 for e in events)
