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
async def test_image_flow_records_ledger_and_url(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL"); assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)
    monkeypatch.setattr(helpers, "engine", engine, raising=False)
    async with engine.begin() as conn: await conn.run_sync(SQLModel.metadata.create_all)

    async with AsyncSession(engine, expire_on_commit=False) as s:
        user = m.AppUser(telegram_id=721000002); s.add(user); await s.commit(); await s.refresh(user)
        convo = m.Conversation(user_id=user.id, title="T", model="gpt-5-nano"); s.add(convo); await s.commit(); await s.refresh(convo)
        msg = m.Message(conversation_id=convo.id, role="assistant"); s.add(msg); await s.commit(); await s.refresh(msg)

    # 1x1 PNG b64
    b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAAWgmWQ0AAAAASUVORK5CYII="

    async def fake_stream(*a, **kw):
        yield {"type":"part.start","index":1,"content_type":"image"}
        yield {"type":"image.ready","index":1,"format":"b64","data":b64}
        yield {"type":"done"}

    monkeypatch.setattr(helpers, "stream_normalized_openai_response", fake_stream, raising=True)

    async def fake_upload(data, prefix="gen"):
        return "https://cdn.example/img.png"

    # mock R2: don’t hit network in unit test
    monkeypatch.setattr(helpers, "upload_openai_image_to_r2", fake_upload, raising=False)

    req_id = str(uuid.uuid4())
    async with AsyncSession(engine, expire_on_commit=False) as s:
        await reserve_request(s, user_id=user.id, conversation_id=convo.id, assistant_message_id=msg.id,
                              request_id=req_id, model_name="gpt-5-nano", feature="text", cost=1, tool_choice="auto")

    class Bus:
        async def publish(self,*a,**k): pass
        async def mark_done(self,*a,**k): pass

    await helpers.generate_and_publish(
        conversation_id=convo.id, assistant_message_id=msg.id, user_id=user.id,
        history_for_openai=[{"role":"user","content":[{"type":"input_text","text":"draw"}]}],
        bus=Bus(), instructions="You are helpful.", model="gpt-5-nano", tool_choice="auto",
        request_id=req_id, tools=[]
    )

    async with AsyncSession(engine, expire_on_commit=False) as s:
        imgs = (await s.exec(select(RequestLedger).where(
            RequestLedger.user_id==user.id, RequestLedger.feature=="image"
        ))).all()
        assert len(imgs) == 1 and imgs[0].state == app.db.models.State.consumed

        url = (await s.exec(select(m.MessageContent).where(
            m.MessageContent.message_id==msg.id, m.MessageContent.type=="image_url"
        ))).first()
        assert url and url.value.startswith("https://")
