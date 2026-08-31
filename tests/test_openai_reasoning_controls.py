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
    assert captured.get("reasoning_effort") == "none"


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
    assert captured.get("reasoning_summary") is None
    assert captured.get("reasoning_effort") == "medium"


@pytest.mark.asyncio
async def test_explicit_openai_reasoning_effort_wins_over_boolean_default(monkeypatch):
    captured: dict = {}

    async def _fake_openai_stream(_messages, _model, **kwargs):
        captured.update(kwargs)
        yield {"type": "done"}

    monkeypatch.setattr(ai_service, "stream_normalized_openai_response", _fake_openai_stream, raising=True)

    async for _ in ai_service.stream_normalized_ai_response(
        [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        model="gpt-5.6-sol",
        thinking_enabled=True,
        reasoning_effort="max",
    ):
        pass

    assert captured.get("reasoning_effort") == "max"


def test_openai_kwargs_include_max_reasoning_and_safety_identifier():
    user_id = uuid.uuid4()
    safety_identifier = openai_service._build_safety_identifier(user_id)

    kwargs = openai_service._build_responses_create_kwargs(
        model="gpt-5.6-sol",
        input_data=[],
        stream=True,
        reasoning_effort="max",
        safety_identifier=safety_identifier,
    )

    assert kwargs["reasoning"] == {"effort": "max"}
    assert kwargs["service_tier"] == "default"
    assert kwargs["safety_identifier"] == safety_identifier
    assert safety_identifier != str(user_id)
    assert safety_identifier == openai_service._build_safety_identifier(user_id)


def test_openai_usage_tracker_splits_reasoning_from_total_output():
    tracker = openai_service.UsageTracker()
    usage = SimpleNamespace(
        input_tokens=11,
        output_tokens=13,
        output_tokens_details=SimpleNamespace(reasoning_tokens=7),
    )

    tracker.apply_completed_event(SimpleNamespace(response=SimpleNamespace(usage=usage)))

    assert tracker.output_tokens == 6
    assert tracker.reasoning_tokens == 7


@pytest.mark.asyncio
async def test_openai_reasoning_text_events_are_not_exposed(monkeypatch):
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

    assert not any(e.get("type") == "reasoning.summary.delta" for e in events)
    assert not any(e.get("type") == "reasoning.summary.done" for e in events)
    assert any(e.get("type") == "done" for e in events)


def test_openai_web_search_requests_sources_and_maps_public_activity():
    kwargs = openai_service._build_responses_create_kwargs(
        model="gpt-5.6-terra",
        input_data=[],
        tools=[{"type": "web_search"}],
        stream=True,
    )
    assert kwargs["include"] == ["web_search_call.action.sources"]

    item = SimpleNamespace(
        id="ws_123",
        action=SimpleNamespace(
            type="open_page",
            url="https://example.com/report",
            sources=[SimpleNamespace(url="https://example.com/report", title="Report")],
        ),
    )
    event = openai_service._web_search_activity_event(item)

    assert event == {
        "type": "web_search.activity",
        "provider": "openai",
        "status": "completed",
        "action": "open_page",
        "item_id": "ws_123",
        "url": "https://example.com/report",
        "sources": [{"url": "https://example.com/report", "title": "Report"}],
    }


@pytest.mark.asyncio
async def test_openai_web_search_item_is_visible_while_page_is_opened():
    item = SimpleNamespace(
        id="ws_live",
        type="web_search_call",
        action=SimpleNamespace(
            type="open_page",
            url="https://example.com/live",
            sources=[],
        ),
    )
    mapped = await openai_service._map_openai_event(
        event=SimpleNamespace(type="response.output_item.added", item=item),
        state=openai_service.StreamState(),
        usage=openai_service.UsageTracker(),
    )

    assert mapped == [
        {
            "type": "web_search.activity",
            "provider": "openai",
            "status": "active",
            "action": "open_page",
            "item_id": "ws_live",
            "url": "https://example.com/live",
        }
    ]
