from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
import httpx
import os
import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

import app.api.images as image_api
from app.api.dependencies import get_current_user
from app.db.database import get_session
from app.db.models import AppUser, Conversation, Message, MessageContent


class _FakeUpstreamResponse:
    def __init__(self, *, url: str, status_code: int = 200, content_type: str = "image/png", body: bytes = b"png-bytes"):
        self.url = httpx.URL(url)
        self.status_code = status_code
        self.headers = {
            "content-type": content_type,
            "content-length": str(len(body)),
            "cache-control": "public, max-age=300",
            "etag": "abc123",
        }
        self._body = body
        self.closed = False

    async def aiter_bytes(self, chunk_size: int = 65536):
        yield self._body

    async def aclose(self):
        self.closed = True


class _FakeAsyncClient:
    def __init__(self, response: _FakeUpstreamResponse):
        self._response = response
        self.closed = False

    def build_request(self, method: str, url: str, headers: dict | None = None):
        return httpx.Request(method, url, headers=headers or {})

    async def send(self, request, stream: bool = True):
        return self._response

    async def aclose(self):
        self.closed = True


class _EmptyResult:
    def first(self):
        return None


class _NoAssetSession:
    async def exec(self, *_args, **_kwargs):
        return _EmptyResult()


def _build_test_client() -> TestClient:
    app = FastAPI()
    app.include_router(image_api.images, prefix="/api/v1")

    async def _fake_get_session():
        yield _NoAssetSession()

    app.dependency_overrides[get_session] = _fake_get_session
    return TestClient(app)


def _build_image_share_app(engine, user: AppUser) -> FastAPI:
    app = FastAPI()
    app.include_router(image_api.images, prefix="/api/v1")

    async def _fake_get_session():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_session] = _fake_get_session
    return app


async def _create_image_content(session: AsyncSession, telegram_id: int) -> tuple[AppUser, MessageContent]:
    user = AppUser(telegram_id=telegram_id)
    session.add(user)
    await session.commit()
    await session.refresh(user)

    conversation = Conversation(user_id=user.id, title="Image share")
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)

    message = Message(conversation_id=conversation.id, role="assistant")
    session.add(message)
    await session.commit()
    await session.refresh(message)

    content = MessageContent(
        message_id=message.id,
        ordinal=0,
        type="image_url",
        value="https://cdn.example/cat.png",
    )
    session.add(content)
    await session.commit()
    await session.refresh(content)

    return user, content


def test_proxy_image_streams_binary(monkeypatch):
    monkeypatch.setenv("IMAGE_FETCH_PROXY_ALLOWED_HOSTS", "allowed.example")
    monkeypatch.setattr(image_api.Settings, "R2_PUBLIC_BASE_URL", "https://allowed.example/")

    capture = {}
    upstream = _FakeUpstreamResponse(url="https://allowed.example/cat.png", body=b"abc123")

    def _client_factory(*args, **kwargs):
        client = _FakeAsyncClient(response=upstream)
        capture["client"] = client
        return client

    monkeypatch.setattr(image_api.httpx, "AsyncClient", _client_factory)

    client = _build_test_client()
    response = client.get("/api/v1/images/proxy", params={"url": "https://allowed.example/cat.png"})

    assert response.status_code == 200
    assert response.content == b"abc123"
    assert response.headers["content-type"].startswith("image/png")
    assert response.headers["content-disposition"] == 'inline; filename="cat.png"'
    assert response.headers["etag"] == "abc123"
    assert capture["client"].closed is True
    assert upstream.closed is True


def test_proxy_image_rejects_disallowed_host(monkeypatch):
    monkeypatch.setenv("IMAGE_FETCH_PROXY_ALLOWED_HOSTS", "allowed.example")
    monkeypatch.setattr(image_api.Settings, "R2_PUBLIC_BASE_URL", "https://allowed.example/")

    client = _build_test_client()
    response = client.get("/api/v1/images/proxy", params={"url": "https://evil.example/cat.png"})

    assert response.status_code == 403
    assert response.json()["detail"] == "Host is not allowed for image proxy"


def test_proxy_image_rejects_non_image_content(monkeypatch):
    monkeypatch.setenv("IMAGE_FETCH_PROXY_ALLOWED_HOSTS", "allowed.example")
    monkeypatch.setattr(image_api.Settings, "R2_PUBLIC_BASE_URL", "https://allowed.example/")

    upstream = _FakeUpstreamResponse(url="https://allowed.example/file.txt", content_type="text/plain", body=b"text")

    def _client_factory(*args, **kwargs):
        return _FakeAsyncClient(response=upstream)

    monkeypatch.setattr(image_api.httpx, "AsyncClient", _client_factory)

    client = _build_test_client()
    response = client.get("/api/v1/images/proxy", params={"url": "https://allowed.example/file.txt"})

    assert response.status_code == 415
    assert response.json()["detail"] == "URL did not return an image"


class _FakeTelegramResponse:
    def __init__(self, *, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _FakeTelegramClient:
    def __init__(self, *, response: _FakeTelegramResponse | None = None, post_exc: Exception | None = None):
        self._response = response
        self._post_exc = post_exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *args, **kwargs):
        if self._post_exc is not None:
            raise self._post_exc
        return self._response


@pytest.mark.asyncio
async def test_prepare_share_returns_502_on_upstream_failure(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user, content = await _create_image_content(session, 987650001)

    monkeypatch.setenv("BOT_TOKEN", "live_token")

    async def _fake_get_bot_username():
        return "test_bot"

    monkeypatch.setattr(image_api, "_get_bot_username", _fake_get_bot_username)
    monkeypatch.setattr(
        image_api.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeTelegramClient(
            response=_FakeTelegramResponse(status_code=500, payload={"ok": False}, text="boom")
        ),
    )

    app = _build_image_share_app(engine, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/images/{content.id}/prepare-share")

    assert response.status_code == 502
    assert response.json()["detail"] == "Telegram prepare-share failed"
    await engine.dispose()


@pytest.mark.asyncio
async def test_prepare_share_returns_502_when_request_raises(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user, content = await _create_image_content(session, 987650002)

    monkeypatch.setenv("BOT_TOKEN", "live_token")

    async def _fake_get_bot_username():
        return "test_bot"

    monkeypatch.setattr(image_api, "_get_bot_username", _fake_get_bot_username)
    monkeypatch.setattr(
        image_api.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeTelegramClient(post_exc=RuntimeError("network down")),
    )

    app = _build_image_share_app(engine, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/images/{content.id}/prepare-share")

    assert response.status_code == 502
    assert response.json()["detail"] == "Telegram prepare-share unavailable"
    await engine.dispose()
