import os, base64, pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import SQLModel, select

import app.api.helpers as helpers
from app.db import models as m

pytestmark = pytest.mark.skipif(os.getenv("R2_TEST_LIVE") != "1", reason="Set R2_TEST_LIVE=1 in .env.test")

@pytest.mark.asyncio
async def test_generate_and_publish_with_b64_image_real_r2(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url, "TEST_DATABASE_URL must be set in .env.test"

    engine = create_async_engine(test_db_url, future=True, echo=False)
    monkeypatch.setattr(helpers, "engine", engine, raising=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = m.AppUser(telegram_id=10_000_000_001)
        session.add(user); await session.commit(); await session.refresh(user)
        convo = m.Conversation(user_id=user.id, title="Test", model="gpt-5-nano")
        session.add(convo); await session.commit(); await session.refresh(convo)
        msg = m.Message(conversation_id=convo.id, role="assistant")
        session.add(msg); await session.commit(); await session.refresh(msg)

    # Build b64 from asset
    png_path = os.path.join(os.path.dirname(__file__), "assets", "test.png")
    with open(png_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")

    # Fake the OpenAI stream → emits base64 image
    async def fake_stream(*a, **kw):
        yield {"type": "part.start", "index": 1, "content_type": "image"}
        yield {"type": "image.ready", "index": 1, "format": "b64", "data": b64}
        yield {"type": "done"}

    class Bus:
        def __init__(self): self.events=[]; self._done=None
        async def publish(self, mid, ev): self.events.append(ev)
        async def mark_done(self, mid, ok=True, error=None): self._done=(ok,error)

    monkeypatch.setattr(helpers, "stream_normalized_openai_response", fake_stream, raising=True)
    await helpers.generate_and_publish(
        conversation_id=convo.id,
        assistant_message_id=msg.id,
        user_id=user.id,
        history_for_openai=[{"role":"user","content":[{"type":"input_text","text":"create an image of a cat"}]}],
        bus=Bus(),
        instructions="You are helpful.",
        model="gpt-5-nano",
        tool_choice="auto",
        tools=[],
    )

    # Read back the image URL
    async with AsyncSession(engine, expire_on_commit=False) as session:
        res = await session.exec(
            select(m.MessageContent).where(
                m.MessageContent.message_id == msg.id,
                m.MessageContent.type == "image_url"
            )
        )
        row = res.first()
        assert row is not None
        print(f"\nR2 URL (stream→upload→db): {row.value}")  # <— full URL printed
