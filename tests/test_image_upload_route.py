import io
from types import SimpleNamespace
import uuid

import pytest
from fastapi import BackgroundTasks, HTTPException, UploadFile
from starlette.datastructures import Headers

import app.api.images as image_api


class _FakeSession:
    def __init__(self):
        self.committed = False
        self.refreshed = []

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        self.refreshed.append(obj)


@pytest.mark.asyncio
async def test_upload_image_returns_processing_and_schedules_probe(monkeypatch):
    session = _FakeSession()
    user = SimpleNamespace(id=uuid.uuid4())
    asset = SimpleNamespace(id=uuid.uuid4())
    background_tasks = BackgroundTasks()

    async def _fake_object_prefix_for_user(_session, _user_id, _source):
        return "images/free/uploaded"

    async def _fake_upload_fileobject(key, file, content_type=None, extra_metadata=None):
        assert key.endswith(".png")
        assert content_type == "image/png"
        assert extra_metadata == {"author": str(user.id), "type": "image"}
        return "bucket-name", key

    async def _fake_create_image_asset(*_args, **_kwargs):
        assert _kwargs["initial_status"] == image_api.IMAGE_STATUS_PROCESSING
        return asset

    monkeypatch.setattr(image_api, "object_prefix_for_user", _fake_object_prefix_for_user)
    monkeypatch.setattr(image_api, "upload_fileobject", _fake_upload_fileobject)
    monkeypatch.setattr(image_api, "create_image_asset", _fake_create_image_asset)
    monkeypatch.setattr(image_api, "serialize_image_asset", lambda _asset: {"status": "processing", "expires_at": None, "retention_policy": "free_30d"})
    monkeypatch.setattr(image_api.Settings, "R2_PUBLIC_BASE_URL", "https://user.example/images/", raising=False)
    monkeypatch.setattr(image_api.Settings, "R2_OPENAI_PUBLIC_BASE_URL", "https://provider.example/images/", raising=False)

    upload = UploadFile(
        filename="cat.png",
        file=io.BytesIO(b"png-bytes"),
        headers=Headers({"content-type": "image/png"}),
    )
    result = await image_api.upload_image(upload, background_tasks, user, session)
    await upload.close()

    assert result.url == "https://user.example/images/" + result.key
    assert result.status == "processing"
    assert session.committed is True
    assert asset in session.refreshed
    assert len(background_tasks.tasks) == 1
    assert background_tasks.tasks[0].func is image_api._refresh_uploaded_image_readiness
    assert background_tasks.tasks[0].args == (asset.id,)


@pytest.mark.asyncio
async def test_get_image_asset_promotes_processing_asset_when_refresh_succeeds(monkeypatch):
    session = _FakeSession()
    user = SimpleNamespace(id=uuid.uuid4())
    asset = SimpleNamespace(
        id=uuid.uuid4(),
        status="processing",
        public_url="https://user.example/images/test.png",
        expires_at=None,
        retention_policy="free_30d",
        source="uploaded",
    )

    async def _fake_find_asset_by_id_or_content_id(_session, _id, user_id=None):
        return asset, None

    async def _fake_refresh_processing_image_asset(_session, current_asset, **_kwargs):
        current_asset.status = "active"
        return True

    monkeypatch.setattr(image_api, "find_asset_by_id_or_content_id", _fake_find_asset_by_id_or_content_id)
    monkeypatch.setattr(image_api, "refresh_processing_image_asset", _fake_refresh_processing_image_asset)
    monkeypatch.setattr(
        image_api,
        "serialize_image_asset",
        lambda current_asset: {
            "id": str(current_asset.id),
            "url": current_asset.public_url,
            "status": current_asset.status,
            "expires_at": None,
            "retention_policy": current_asset.retention_policy,
            "source": current_asset.source,
            "unavailable_reason": None,
        },
    )

    response = await image_api.get_image_asset(asset.id, session, user)

    assert response.status == "active"
