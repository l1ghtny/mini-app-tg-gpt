import os
from datetime import timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

import app.api.images as image_api
from app.api.dependencies import get_current_user
from app.api.helpers import save_image_url_to_db
from app.db.database import get_session
from app.db.models import AppUser, Conversation, ImageAsset, Message, MessageContent, utcnow_naive


async def _create_user_message(session: AsyncSession, telegram_id: int):
    user = AppUser(telegram_id=telegram_id)
    session.add(user)
    await session.commit()
    await session.refresh(user)

    conversation = Conversation(user_id=user.id, title="Images")
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)

    message = Message(conversation_id=conversation.id, role="assistant")
    session.add(message)
    await session.commit()
    await session.refresh(message)

    return user, conversation, message


def _build_image_app(engine, user: AppUser) -> FastAPI:
    app = FastAPI()
    app.include_router(image_api.images, prefix="/api/v1")

    async def _fake_get_session():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_session] = _fake_get_session
    return app


@pytest.mark.asyncio
async def test_save_generated_image_creates_asset_metadata(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    monkeypatch.setenv("IMAGE_FREE_RETENTION_DAYS", "30")

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user, conversation, message = await _create_user_message(session, 987660001)
        content, asset = await save_image_url_to_db(
            "https://cdn.example/bucket/images/free/generated/aa/cat.png",
            0,
            message.id,
            session=session,
            user_id=user.id,
            conversation_id=conversation.id,
            bucket="bucket",
            key="images/free/generated/aa/cat.png",
            source="generated",
        )

    async with AsyncSession(engine, expire_on_commit=False) as session:
        stored_asset = (await session.exec(select(ImageAsset))).first()
        stored_content = await session.get(MessageContent, content.id)

    assert asset is not None
    assert stored_asset is not None
    assert stored_asset.message_content_id == content.id
    assert stored_asset.retention_policy == "free_30d"
    assert stored_asset.expires_at is not None
    assert stored_content.data["image"]["id"] == str(stored_asset.id)
    assert stored_content.data["image"]["status"] == "active"
    await engine.dispose()


@pytest.mark.asyncio
async def test_expired_image_metadata_blocks_status_and_share(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user, conversation, message = await _create_user_message(session, 987660002)
        content = MessageContent(
            message_id=message.id,
            ordinal=0,
            type="image_url",
            value="https://cdn.example/bucket/images/free/generated/aa/expired.png",
        )
        session.add(content)
        await session.flush()
        asset = ImageAsset(
            user_id=user.id,
            conversation_id=conversation.id,
            message_content_id=content.id,
            bucket="bucket",
            key="images/free/generated/aa/expired.png",
            public_url=content.value,
            source="generated",
            retention_policy="free_30d",
            status="active",
            expires_at=utcnow_naive() - timedelta(days=1),
        )
        session.add(asset)
        await session.commit()
        await session.refresh(asset)

    app = _build_image_app(engine, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        status_response = await client.get(f"/api/v1/images/{asset.id}")
        share_response = await client.post(f"/api/v1/images/{asset.id}/prepare-share")

    assert status_response.status_code == 200
    assert status_response.json()["status"] == "expired"
    assert share_response.status_code == 410
    assert share_response.json()["detail"] == "Image expired"
    await engine.dispose()
