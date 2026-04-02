import asyncio
import os, uuid, pytest
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select

import app.api.helpers as helpers
import app.db.models
from app.db import models as m
from app.db.models import RequestLedger
from app.services.subscription_check.entitlements import reserve_request

@pytest.mark.asyncio
async def test_text_request_reserve_and_consume(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL"); assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)
    monkeypatch.setattr(helpers, "engine", engine, raising=False)
    async with engine.begin() as conn: await conn.run_sync(SQLModel.metadata.create_all)

    async with AsyncSession(engine, expire_on_commit=False) as s:
        user = m.AppUser(telegram_id=721000001); s.add(user); await s.commit(); await s.refresh(user)
        convo = m.Conversation(user_id=user.id, title="T", model="gpt-5-nano"); s.add(convo); await s.commit(); await s.refresh(convo)
        msg = m.Message(conversation_id=convo.id, role="assistant"); s.add(msg); await s.commit(); await s.refresh(msg)

    # Fake a tiny text stream
    async def fake_stream(*a, **kw):
        yield {"type":"part.start","index":0,"content_type":"text"}
        yield {"type":"text.delta","index":0,"text":"hi"}
        yield {"type":"text.done","index":0}
        yield {"type":"done"}

    monkeypatch.setattr(helpers, "stream_normalized_openai_response", fake_stream, raising=True)

    req_id = str(uuid.uuid4())
    async with AsyncSession(engine, expire_on_commit=False) as s:
        await reserve_request(s, user_id=user.id, conversation_id=convo.id, assistant_message_id=msg.id,
                              request_id=req_id, model_name="gpt-5-nano", feature="text", cost=1, tool_choice="auto")

    class Bus:
        async def publish(self,*a,**k): pass
        async def mark_done(self,*a,**k): pass

    await helpers.generate_and_publish(
        conversation_id=convo.id, assistant_message_id=msg.id, user_id=user.id,
        history_for_openai=[{"role":"user","content":[{"type":"input_text","text":"hello"}]}],
        bus=Bus(), instructions="You are helpful.", model="gpt-5-nano", tool_choice="auto",
        request_id=req_id, tools=[]
    )

    async with AsyncSession(engine, expire_on_commit=False) as s:
        rl = (await s.exec(select(RequestLedger).where(
            RequestLedger.user_id==user.id, RequestLedger.request_id==req_id
        ))).first()
        assert rl and rl.state == app.db.models.State.consumed  # adjust if State is imported elsewhere

        # Optional: assert TokenUsage was logged
        tu = (await s.exec(select(m.TokenUsage).where(m.TokenUsage.user_id==user.id))).first()
        # assert tu is not None
