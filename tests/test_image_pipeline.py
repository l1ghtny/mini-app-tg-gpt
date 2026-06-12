import base64
import uuid
import pytest

import app.api.helpers as helpers

class FakeBus:
    def __init__(self):
        self.events = []
        self.done = None
    async def publish(self, mid: str, event: dict):
        self.events.append(("publish", mid, event))
    async def mark_done(self, mid: str, ok: bool = True, error: str | None = None):
        self.done = (mid, ok, error)
        self.events.append(("publish", mid, {"type": "done"} if ok else {"type": "error", "error": error}))

class _DummySession:
    async def exec(self, *a, **kw): return type("R", (), {"first": lambda self: None})()
    async def commit(self): pass
    def add(self, x): pass
    async def refresh(self, *a, **kw): pass

class FakeAsyncSessionCtx:
    """Drop-in replacement for helpers.AsyncSession(engine, ...) context manager."""
    def __init__(self, *a, **kw): pass
    async def __aenter__(self): return _DummySession()
    async def __aexit__(self, exc_type, exc, tb): return False

@pytest.mark.asyncio
async def test_generate_and_publish_uploads_b64_and_persists_url(monkeypatch):
    # ---- 1) Fake adapter to yield base64 image as you do now ----
    # part.start (image), then image.ready with base64 payload, then done
    fake_png_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n\x00\x00fake").decode("ascii")
    async def fake_stream_normalized_ai_response(*a, **kw):
        # text not required here; just image path
        yield {"type": "part.start", "index": 999, "content_type": "image"}
        yield {"type": "image.partial", "index": 999, "format": "b64", "data": "partial-b64", "partial_index": 0, "sequence_number": 1}
        yield {"type": "image.ready", "index": 999, "format": "b64", "data": fake_png_b64}
        yield {"type": "done"}

    monkeypatch.setattr(helpers, "stream_normalized_ai_response",
                        fake_stream_normalized_ai_response)

    # ---- 2) Stub out AsyncSession context manager (no real DB) ----
    monkeypatch.setattr(helpers, "AsyncSession", FakeAsyncSessionCtx)

    # ---- 3) Spy the two functions we want to verify ----
    captured = {
        "upload_calls": [],
        "saved": None,
        "deleted_keys": [],
    }

    async def fake_upload_openai_image_to_r2_with_key(b64_png: str, prefix: str = "gen", suffix: str | None = None):
        captured["upload_calls"].append((b64_png, prefix, suffix))
        if prefix == "images/partial":
            return "https://public.cdn.example/bucket/images/partial/aa/partial.png", "images/partial/aa/partial.png"
        return "https://public.cdn.example/bucket/images/free/generated/aa/final.png", "images/free/generated/aa/final.png"

    async def fake_save_image_url_to_db(image_url: str, ordinal: int, message_id, **_kwargs):
        captured["saved"] = (image_url, ordinal, message_id)

    async def fake_delete_object(key: str):
        captured["deleted_keys"].append(key)

    monkeypatch.setattr(helpers, "upload_openai_image_to_r2_with_key", fake_upload_openai_image_to_r2_with_key)
    monkeypatch.setattr(helpers, "delete_object", fake_delete_object)
    monkeypatch.setattr(helpers, "save_image_url_to_db", fake_save_image_url_to_db)

    async def fake_object_prefix_for_user(*_args, **_kwargs):
        return "images/free/generated"

    monkeypatch.setattr(helpers, "object_prefix_for_user", fake_object_prefix_for_user)

    # ---- 4) Drive the function under test ----
    conversation_id = uuid.uuid4()
    assistant_message_id = uuid.uuid4()
    user_id = uuid.uuid4()
    history_for_openai = [{"role": "user", "content": [{"type": "input_text", "text": "draw cat"}]}]
    bus = FakeBus()

    await helpers.generate_and_publish(
        conversation_id=conversation_id,
        assistant_message_id=assistant_message_id,
        user_id=user_id,
        history_for_openai=history_for_openai,
        bus=bus,
        instructions="You are a helpful assistant.",
        model="gpt-5.4-nano",
        tool_choice="auto",
        tools=[],
    )

    # ---- 5) Assertions ----
    # a) Upload function got both partial and final base64 payloads
    assert len(captured["upload_calls"]) == 2
    assert captured["upload_calls"][0][0] == "partial-b64"
    assert captured["upload_calls"][0][1] == "images/partial"
    assert captured["upload_calls"][1][0] == fake_png_b64
    assert captured["upload_calls"][1][1] == "images/free/generated"

    # b) DB saver called with returned URL and your chosen ordinal (index)
    assert captured["saved"] is not None
    url, ordinal, mid = captured["saved"]
    assert url == "https://public.cdn.example/bucket/images/free/generated/aa/final.png"
    assert ordinal == 999
    assert mid == assistant_message_id

    # c) Partial and final image URL events were published for the frontend stream
    published_events = [event for kind, _mid, event in bus.events if kind == "publish"]
    assert any(event.get("type") == "image.partial_url" and event.get("url") == "https://public.cdn.example/bucket/images/partial/aa/partial.png" for event in published_events)
    assert any(event.get("type") == "image.url" and event.get("url") == url for event in published_events)
    assert "images/partial/aa/partial.png" in captured["deleted_keys"]

    # c) Lifecycle completed
    assert bus.done[1] is True


@pytest.mark.asyncio
async def test_generate_and_publish_final_image_storage_failure_surfaces_structured_error(monkeypatch):
    fake_png_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n\x00\x00fake").decode("ascii")

    async def fake_stream_normalized_ai_response(*a, **kw):
        yield {"type": "image.ready", "index": 7, "format": "b64", "data": fake_png_b64}

    monkeypatch.setattr(helpers, "stream_normalized_ai_response", fake_stream_normalized_ai_response)
    monkeypatch.setattr(helpers, "AsyncSession", FakeAsyncSessionCtx)

    async def failing_upload(*args, **kwargs):
        raise RuntimeError("r2 write failed")

    monkeypatch.setattr(helpers, "upload_openai_image_to_r2_with_key", failing_upload)

    async def fake_object_prefix_for_user(*_args, **_kwargs):
        return "images/free/generated"

    monkeypatch.setattr(helpers, "object_prefix_for_user", fake_object_prefix_for_user)

    conversation_id = uuid.uuid4()
    assistant_message_id = uuid.uuid4()
    user_id = uuid.uuid4()
    bus = FakeBus()

    with pytest.raises(RuntimeError, match="r2 write failed"):
        await helpers.generate_and_publish(
            conversation_id=conversation_id,
            assistant_message_id=assistant_message_id,
            user_id=user_id,
            history_for_openai=[{"role": "user", "content": [{"type": "input_text", "text": "draw cat"}]}],
            bus=bus,
            instructions="You are a helpful assistant.",
            model="gpt-5.4-nano",
            tool_choice="auto",
            tools=[],
        )

    published_events = [event for kind, _mid, event in bus.events if kind == "publish"]
    assert any(
        event.get("type") == "error"
        and event.get("code") == helpers.IMAGE_STORAGE_ERROR_CODE
        and event.get("error") == helpers.IMAGE_STORAGE_ERROR_MESSAGE
        for event in published_events
    )
    assert bus.done is not None
    assert bus.done[1] is False
    assert bus.done[2] == helpers.IMAGE_STORAGE_ERROR_MESSAGE


@pytest.mark.asyncio
async def test_generate_and_publish_ignores_partial_image_storage_failure(monkeypatch):
    fake_png_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n\x00\x00fake").decode("ascii")

    async def fake_stream_normalized_ai_response(*a, **kw):
        yield {"type": "image.partial", "index": 4, "format": "b64", "data": "partial-b64", "partial_index": 0, "sequence_number": 1}
        yield {"type": "image.ready", "index": 4, "format": "b64", "data": fake_png_b64}
        yield {"type": "done"}

    monkeypatch.setattr(helpers, "stream_normalized_ai_response", fake_stream_normalized_ai_response)
    monkeypatch.setattr(helpers, "AsyncSession", FakeAsyncSessionCtx)

    captured = {"saved": None}

    async def flaky_upload(b64_png: str, prefix: str = "gen", suffix: str | None = None):
        if suffix is not None:
            raise RuntimeError("partial store failed")
        return "https://public.cdn.example/bucket/images/free/generated/aa/final.png", "images/free/generated/aa/final.png"

    async def fake_save_image_url_to_db(image_url: str, ordinal: int, message_id, **_kwargs):
        captured["saved"] = (image_url, ordinal, message_id)

    async def fake_object_prefix_for_user(*_args, **_kwargs):
        return "images/free/generated"

    monkeypatch.setattr(helpers, "upload_openai_image_to_r2_with_key", flaky_upload)
    monkeypatch.setattr(helpers, "save_image_url_to_db", fake_save_image_url_to_db)
    monkeypatch.setattr(helpers, "object_prefix_for_user", fake_object_prefix_for_user)

    conversation_id = uuid.uuid4()
    assistant_message_id = uuid.uuid4()
    user_id = uuid.uuid4()
    bus = FakeBus()

    await helpers.generate_and_publish(
        conversation_id=conversation_id,
        assistant_message_id=assistant_message_id,
        user_id=user_id,
        history_for_openai=[{"role": "user", "content": [{"type": "input_text", "text": "draw cat"}]}],
        bus=bus,
        instructions="You are a helpful assistant.",
        model="gpt-5.4-nano",
        tool_choice="auto",
        tools=[],
    )

    published_events = [event for kind, _mid, event in bus.events if kind == "publish"]
    assert not any(event.get("type") == "image.partial_url" for event in published_events)
    assert any(event.get("type") == "image.url" for event in published_events)
    assert captured["saved"] is not None
    assert bus.done is not None
    assert bus.done[1] is True
