import pytest

from app.services import public_image_reachability


class _Response:
    status_code = 206
    headers = {"content-type": "image/png"}

    async def aiter_bytes(self):
        yield b"image"


class _Stream:
    async def __aenter__(self):
        return _Response()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


@pytest.mark.asyncio
async def test_public_image_probe_ignores_environment_proxy(monkeypatch):
    client_options = {}

    class _Client:
        def __init__(self, **kwargs):
            client_options.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        def stream(self, method, url, *, headers):
            assert method == "GET"
            assert url == "https://tg-bot-images.lightny.pro/image.png"
            assert headers == {"Range": "bytes=0-65535"}
            return _Stream()

    monkeypatch.setattr(public_image_reachability.httpx, "AsyncClient", _Client)

    reachable = await public_image_reachability.wait_for_image_url_reachability(
        "https://tg-bot-images.lightny.pro/image.png",
        max_retries=1,
    )

    assert reachable is True
    assert client_options["trust_env"] is False

