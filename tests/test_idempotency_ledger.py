import os, pytest
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from app.db import models as m
from app.db.models import RequestLedger
from app.services.subscription_check.entitlements import reserve_request

@pytest.mark.asyncio
async def test_reserve_is_idempotent(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL"); assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)
    async with engine.begin() as conn: await conn.run_sync(SQLModel.metadata.create_all)

    async with AsyncSession(engine, expire_on_commit=False) as s:
        user = m.AppUser(telegram_id=721000003); s.add(user); await s.commit(); await s.refresh(user)
        user_id = user.id
        convo = m.Conversation(user_id=user.id, title="T", model="gpt-5-nano"); s.add(convo); await s.commit(); await s.refresh(convo)
        msg = m.Message(conversation_id=convo.id, role="assistant"); s.add(msg); await s.commit(); await s.refresh(msg)

        req_id = "client-key-xyz"
        rl1 = await reserve_request(s, user_id=user.id, conversation_id=convo.id, assistant_message_id=msg.id,
                                    request_id=req_id, model_name="gpt-5-nano", feature="text", cost=1, tool_choice="auto")
        rl2 = await reserve_request(s, user_id=user.id, conversation_id=convo.id, assistant_message_id=msg.id,
                                    request_id=req_id, model_name="gpt-5-nano", feature="text", cost=1, tool_choice="auto")
        assert rl1.id == rl2.id

        rows = (await s.exec(select(RequestLedger).where(
            RequestLedger.user_id==user_id, RequestLedger.request_id==req_id
        ))).all()
        assert len(rows) == 1
