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
    async def fake_stream_normalized_openai_response(*a, **kw):
        # text not required here; just image path
        yield {"type": "part.start", "index": 999, "content_type": "image"}
        yield {"type": "image.ready", "index": 999, "format": "b64", "data": fake_png_b64}
        yield {"type": "done"}

    monkeypatch.setattr(helpers, "stream_normalized_openai_response",
                        fake_stream_normalized_openai_response)

    # ---- 2) Stub out AsyncSession context manager (no real DB) ----
    monkeypatch.setattr(helpers, "AsyncSession", FakeAsyncSessionCtx)

    # ---- 3) Spy the two functions we want to verify ----
    captured = {"upload_called_with": None, "saved": None}

    async def fake_upload_openai_image_to_r2(b64_png: str, prefix: str = "gen"):
        captured["upload_called_with"] = b64_png
        # Simulate R2 public URL (helpers.upload_openai_image_to_r2 builds this from put_bytes + Settings)
        # See real function for reference :contentReference[oaicite:1]{index=1}
        return "https://public.cdn.example/bucket/gen/aa/sha.png"

    async def fake_save_image_url_to_db(image_url: str, ordinal: int, message_id):
        captured["saved"] = (image_url, ordinal, message_id)

    monkeypatch.setattr(helpers, "upload_openai_image_to_r2", fake_upload_openai_image_to_r2)
    monkeypatch.setattr(helpers, "save_image_url_to_db", fake_save_image_url_to_db)

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
        model="gpt-5-nano",
        tool_choice="auto",
    )

    # ---- 5) Assertions ----
    # a) Upload function got base64
    assert captured["upload_called_with"] == fake_png_b64

    # b) DB saver called with returned URL and your chosen ordinal (index)
    assert captured["saved"] is not None
    url, ordinal, mid = captured["saved"]
    assert url == "https://public.cdn.example/bucket/gen/aa/sha.png"
    assert ordinal == 999
    assert mid == assistant_message_id

    # c) Lifecycle completed
    assert bus.done[1] is True
