import os
import uuid

import pytest
from botocore.exceptions import ClientError
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db import models as m
from app.r2.settings import Settings
from app.services.background import image_deriver


def test_key_from_public_url_accepts_user_and_openai_bases(monkeypatch):
    monkeypatch.setattr(Settings, "R2_PUBLIC_BASE_URL", "https://lightny.ru/images/", raising=False)
    monkeypatch.setattr(Settings, "R2_OPENAI_PUBLIC_BASE_URL", "https://tg-bot-images.lightny.pro/", raising=False)
    monkeypatch.setattr(image_deriver, "R2_BUCKET", "tg-bot-images", raising=False)

    key = "images/free/uploaded/2026/06/14/test.png"
    proxied_url = f"https://lightny.ru/images/{key}"
    legacy_proxied_url = f"https://lightny.ru/images/tg-bot-images/{key}"
    openai_url = f"https://tg-bot-images.lightny.pro/{key}"
    legacy_openai_url = f"https://tg-bot-images.lightny.pro/tg-bot-images/{key}"

    assert image_deriver._key_from_public_url(proxied_url) == key
    assert image_deriver._key_from_public_url(legacy_proxied_url) == key
    assert image_deriver._key_from_public_url(openai_url) == key
    assert image_deriver._key_from_public_url(legacy_openai_url) == key


def test_public_url_uses_openai_base_only_for_openai_requests(monkeypatch):
    monkeypatch.setattr(Settings, "R2_PUBLIC_BASE_URL", "https://lightny.ru/images/", raising=False)
    monkeypatch.setattr(Settings, "R2_OPENAI_PUBLIC_BASE_URL", "https://tg-bot-images.lightny.pro/", raising=False)
    monkeypatch.setattr(image_deriver, "R2_BUCKET", "tg-bot-images", raising=False)

    key = "images/free/uploaded/2026/06/14/test.png"

    assert image_deriver._public_url(key) == f"https://lightny.ru/images/{key}"
    assert image_deriver._public_url(key, for_openai=True) == f"https://tg-bot-images.lightny.pro/{key}"


@pytest.mark.asyncio
async def test_ensure_openai_compatible_image_url_marks_missing_asset_on_head_404(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url

    engine = create_async_engine(test_db_url, future=True, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = m.AppUser(telegram_id=721000206)
        session.add(user)
        await session.commit()
        await session.refresh(user)

        conversation = m.Conversation(user_id=user.id, title=f"conv-{uuid.uuid4()}")
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)

        user_message = m.Message(conversation_id=conversation.id, role="user")
        session.add(user_message)
        await session.commit()
        await session.refresh(user_message)

        key = "images/free/uploaded/2026/06/22/missing.png"
        proxied_url = f"https://lightny.ru/images/{key}"
        content = m.MessageContent(message_id=user_message.id, ordinal=0, type="image_url", value=proxied_url)
        session.add(content)
        await session.commit()
        await session.refresh(content)

        asset = m.ImageAsset(
            user_id=user.id,
            conversation_id=conversation.id,
            message_content_id=content.id,
            bucket="tg-bot-images",
            key=key,
            public_url=proxied_url,
            source="uploaded",
            retention_policy="free_30d",
            status="active",
        )
        session.add(asset)
        await session.commit()
        await session.refresh(asset)

        monkeypatch.setattr(Settings, "R2_PUBLIC_BASE_URL", "https://lightny.ru/images/", raising=False)
        monkeypatch.setattr(Settings, "R2_OPENAI_PUBLIC_BASE_URL", "https://tg-bot-images.lightny.pro/", raising=False)

        async def fake_head_object(_key: str):
            raise ClientError(
                {
                    "Error": {"Code": "404", "Message": "Not Found"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "HeadObject",
            )

        monkeypatch.setattr(image_deriver, "head_object", fake_head_object, raising=True)

        with pytest.raises(HTTPException) as exc_info:
            await image_deriver.ensure_openai_compatible_image_url(session, proxied_url, max_size=2048)

        await session.refresh(asset)

    assert exc_info.value.status_code == 410
    assert exc_info.value.detail == "Image unavailable"
    assert asset.status == "missing"

