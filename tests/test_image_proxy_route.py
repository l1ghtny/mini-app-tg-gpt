from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx

import app.api.images as image_api


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


def _build_test_client() -> TestClient:
    app = FastAPI()
    app.include_router(image_api.images, prefix="/api/v1")
    return TestClient(app)


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
