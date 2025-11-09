import os
import uuid
import base64
import pytest

from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine

import app.api.helpers as helpers
from app.db import models as m

pytestmark = pytest.mark.skipif(
    os.getenv("R2_TEST_LIVE") != "1",
    reason="Set R2_TEST_LIVE=1 and configure .env.test to run this live test.",
)

@pytest.mark.asyncio
async def test_r2_upload_and_db_persist_roundtrip(tmp_path):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url, "TEST_DATABASE_URL must be set in .env.test"

    engine = create_async_engine(test_db_url, future=True, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = m.AppUser(telegram_id=10_000_000_000)
        session.add(user); await session.commit(); await session.refresh(user)

        convo = m.Conversation(user_id=user.id, title="Test", model="gpt-5-nano")
        session.add(convo); await session.commit(); await session.refresh(convo)

        msg = m.Message(conversation_id=convo.id, role="assistant")
        session.add(msg); await session.commit(); await session.refresh(msg)

        png_path = os.path.join(os.path.dirname(__file__), "assets", "test.png")
        with open(png_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")

        url = await helpers.upload_openai_image_to_r2(b64, prefix="gen")
        print(f"\nR2 URL (upload): {url}")  # <— full URL printed

        ordinal = 1
        await helpers.save_image_url_to_db(url, ordinal, msg.id)

        res = await session.exec(
            select(m.MessageContent).where(
                m.MessageContent.message_id == msg.id,
                m.MessageContent.ordinal == ordinal,
                m.MessageContent.type == "image_url",
            )
        )
        row = res.first()
        assert row is not None
        assert row.value == url
        print(f"R2 URL (db row): {row.value}")  # <— full URL printed