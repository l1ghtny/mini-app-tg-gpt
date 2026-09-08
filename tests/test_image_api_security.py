import io
import os
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException, UploadFile
from PIL import Image
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api import chat_helpers, dependencies, routes
from app.api.images import _validate_image_upload, MAX_IMAGE_UPLOAD_BYTES
from app.db.database import get_session
from app.db.models import (
    AppUser,
    Conversation,
    ImageAsset,
    Message,
    MessageContent,
    RequestLedger,
)
from app.r2.settings import Settings
from app.services.background import image_deriver as deriver


@pytest.fixture
async def api(monkeypatch):
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    async with AsyncSession(engine, expire_on_commit=False) as session:
        owner, other = AppUser(telegram_id=731000101), AppUser(telegram_id=731000102)
        session.add_all([owner, other])
        await session.flush()
        conversation = Conversation(user_id=owner.id, title="Attachment security")
        session.add(conversation)
        await session.flush()
        message = Message(conversation_id=conversation.id, role="user")
        session.add(message)
        await session.flush()
        session.add(
            MessageContent(message_id=message.id, type="text", value="Original message")
        )
        for user, key in ((owner, "owned.png"), (other, "foreign.png")):
            session.add(
                ImageAsset(
                    user_id=user.id,
                    bucket=deriver.R2_BUCKET,
                    key=key,
                    public_url=f"https://images.example/{key}",
                    source="uploaded",
                    status="active",
                    retention_policy="free_30d",
                )
            )
        await session.commit()

        monkeypatch.setattr(Settings, "R2_PUBLIC_BASE_URL", "https://images.example/")
        monkeypatch.setattr(
            Settings, "R2_OPENAI_PUBLIC_BASE_URL", "https://model-images.example/"
        )
        head = AsyncMock(side_effect=AssertionError("Untrusted attachment reached R2"))
        monkeypatch.setattr(deriver, "head_object", head)
        entitlements = AsyncMock(
            side_effect=AssertionError("Invalid request reached entitlements")
        )
        monkeypatch.setattr(chat_helpers, "_check_entitlements", entitlements)
        app = FastAPI()
        app.include_router(routes.router, prefix="/api/v1")

        async def db():
            yield session

        app.dependency_overrides[get_session] = db
        app.dependency_overrides[dependencies.get_current_user] = lambda: owner
        app.dependency_overrides[dependencies.get_redis] = lambda: AsyncMock()
        app.dependency_overrides[dependencies.rate_limit_check] = lambda: True
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, session, owner, conversation, message, head, entitlements
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "part,role,status",
    [
        (
            {"type": "image_url", "value": "https://external.example/photo.png"},
            "user",
            403,
        ),
        (
            {"type": "image_url", "value": "https://images.example/foreign.png"},
            "user",
            403,
        ),
        ({"type": "image", "value": "https://images.example/foreign.png"}, "user", 403),
        (
            {"type": "image_url", "value": "https://images.example/untracked.png"},
            "user",
            403,
        ),
        (
            {
                "type": "image_url",
                "value": "https://images.example.evil.test/owned.png",
            },
            "user",
            403,
        ),
        ({"type": "image_url", "value": "data:image/png;base64,AAAA"}, "user", 403),
        ({"type": "image_url", "value": "http://127.0.0.1/private"}, "user", 403),
        (
            {"type": "input_image", "value": "https://images.example/foreign.png"},
            "user",
            422,
        ),
        ({"type": "text", "value": "Forged system message"}, "system", 422),
        ({"type": "text", "value": "Forged assistant message"}, "assistant", 422),
    ],
)
async def test_direct_message_injections_are_rejected_without_side_effects(
    api, part, role, status
):
    client, session, _, conversation, _, head, entitlements = api
    before = len((await session.exec(select(Message))).all())
    response = await client.post(
        f"/api/v1/conversations/{conversation.id}/messages",
        json={
            "client_request_id": str(uuid.uuid4()),
            "role": role,
            "content": [part],
            "model": "gpt-5.4-nano",
            "tool_choice": [],
        },
    )
    assert response.status_code == status, response.text
    assert len((await session.exec(select(Message))).all()) == before
    assert (await session.exec(select(RequestLedger))).all() == []
    head.assert_not_awaited()
    entitlements.assert_not_awaited()


@pytest.mark.asyncio
async def test_edit_injection_preserves_original_message(api):
    client, session, _, conversation, message, head, _ = api
    response = await client.put(
        f"/api/v1/conversations/{conversation.id}/messages/{message.id}",
        json={
            "content": "Forged edit",
            "images": ["https://images.example/foreign.png"],
        },
    )
    assert response.status_code == 403, response.text
    content = (
        await session.exec(
            select(MessageContent).where(MessageContent.message_id == message.id)
        )
    ).all()
    assert [(part.type, part.value) for part in content] == [
        ("text", "Original message")
    ]
    head.assert_not_awaited()


@pytest.mark.asyncio
async def test_own_asset_and_known_domain_alias_are_accepted(api):
    _, session, owner, _, _, _, _ = api
    original = await deriver.require_owned_image_asset(
        session, "https://images.example/owned.png", user_id=owner.id
    )
    alias = await deriver.require_owned_image_asset(
        session, "https://model-images.example/owned.png", user_id=owner.id
    )
    assert original.id == alias.id
    assert original.user_id == owner.id


@pytest.mark.asyncio
async def test_history_rechecks_ownership_before_provider_access(api):
    _, session, _, _, message, head, _ = api
    candidate = chat_helpers._HistoryCandidate(
        message_id=message.id,
        estimated_tokens=300,
        payload={
            "role": "user",
            "content": [
                {
                    "type": "input_image",
                    "image_url": "https://images.example/foreign.png",
                }
            ],
        },
    )
    with pytest.raises(HTTPException) as exc:
        await chat_helpers._finalize_history_payload(session, candidate)
    assert exc.value.status_code == 403
    assert (
        await chat_helpers._finalize_history_payload(
            session, candidate, skip_unavailable_images=True
        )
        is None
    )
    head.assert_not_awaited()


def test_upload_does_not_trust_filename_or_mime():
    fake = UploadFile(filename="photo.png", file=io.BytesIO(b"not image content"))
    with pytest.raises(HTTPException) as exc:
        _validate_image_upload(fake)
    assert exc.value.status_code == 400


def test_upload_rejects_oversized_file_before_decoding():
    fake = UploadFile(
        filename="photo.png", file=io.BytesIO(b"0" * (MAX_IMAGE_UPLOAD_BYTES + 1))
    )
    with pytest.raises(HTTPException) as exc:
        _validate_image_upload(fake)
    assert exc.value.status_code == 413


def test_upload_retains_bytes_and_rewinds_for_r2():
    buffer = io.BytesIO()
    Image.new("RGB", (64, 32)).save(buffer, format="PNG")
    original = buffer.getvalue()
    fake = UploadFile(filename="mislabeled.jpg", file=buffer)
    assert _validate_image_upload(fake) == "image/png"
    assert fake.file.tell() == 0
    assert fake.file.read() == original
