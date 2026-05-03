import uuid

import pytest

import app.api.helpers as helpers


class FakeBus:
    def __init__(self):
        self.events = []
        self.done = None

    async def publish(self, mid: str, event: dict):
        self.events.append((mid, event))

    async def mark_done(self, mid: str, ok: bool = True, error: str | None = None):
        self.done = (mid, ok, error)


class _DummySession:
    async def exec(self, *a, **kw):
        return type("R", (), {"first": lambda self: None})()

    async def commit(self):
        pass

    def add(self, _x):
        pass

    async def refresh(self, *_a, **_kw):
        pass


class FakeAsyncSessionCtx:
    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return _DummySession()

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_generate_and_publish_emits_rich_status_and_reasoning_events(monkeypatch):
    async def fake_stream(*a, **kw):
        yield {
            "type": "status",
            "stage": "queued",
            "phase": "response.created",
            "label": "Queued",
            "source_event": "response.created",
            "sequence_number": 1,
            "ts": 123,
        }
        yield {
            "type": "status",
            "stage": "thinking",
            "phase": "reasoning.in_progress",
            "label": "Thinking",
            "source_event": "response.output_item.added",
            "sequence_number": 2,
            "ts": 124,
        }
        yield {
            "type": "reasoning.summary.delta",
            "delta": "Checking constraints...",
            "output_index": 0,
            "summary_index": 0,
            "item_id": "rs_1",
            "sequence_number": 3,
        }
        yield {
            "type": "reasoning.summary.done",
            "text": "Checked constraints and formed an answer plan.",
            "output_index": 0,
            "summary_index": 0,
            "item_id": "rs_1",
            "sequence_number": 4,
        }
        yield {"type": "done"}

    async def fake_finalize_request(*_a, **_kw):
        return None

    monkeypatch.setattr(helpers, "stream_normalized_openai_response", fake_stream, raising=True)
    monkeypatch.setattr(helpers, "AsyncSession", FakeAsyncSessionCtx)
    monkeypatch.setattr(helpers, "finalize_request", fake_finalize_request, raising=True)

    bus = FakeBus()
    await helpers.generate_and_publish(
        conversation_id=uuid.uuid4(),
        assistant_message_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        history_for_openai=[{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        bus=bus,
        instructions="You are helpful.",
        model="gpt-5-nano",
        tool_choice="auto",
        request_id=str(uuid.uuid4()),
        tools=[],
    )

    published = [ev for _mid, ev in bus.events]
    assert any(ev.get("type") == "status" and ev.get("phase") == "response.created" for ev in published)
    assert any(ev.get("type") == "status" and ev.get("phase") == "reasoning.in_progress" for ev in published)
    assert any(ev.get("type") == "reasoning.summary.delta" for ev in published)
    assert any(ev.get("type") == "reasoning.summary.done" for ev in published)
