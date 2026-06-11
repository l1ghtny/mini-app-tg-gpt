import hashlib
import importlib

import httpx
import pytest

import app.core.config as config_module
from app.services.banking.tbank import TBankService


def test_tbank_api_url_can_be_overridden_from_env(monkeypatch):
    monkeypatch.setenv("TBANK_API_URL", "https://rest-api-test.tinkoff.ru/v2")
    reloaded = importlib.reload(config_module)
    try:
        assert reloaded.settings.TBANK_API_URL == "https://rest-api-test.tinkoff.ru/v2"
    finally:
        monkeypatch.delenv("TBANK_API_URL", raising=False)
        importlib.reload(config_module)


def test_generate_token_ignores_nested_fields_like_data_and_receipt():
    service = TBankService()
    service.password = "secret"

    payload = {
        "TerminalKey": "TERM123",
        "Description": "Bind account",
        "DataType": "PAYLOAD",
        "Data": {
            "user_id": "u-1",
            "tier_name": "Advanced",
        },
        "Receipt": {
            "Email": "user@example.com",
        },
    }

    token = service._generate_token(payload)

    expected_source = "PAYLOAD" + "Bind account" + "secret" + "TERM123"
    expected_token = hashlib.sha256(expected_source.encode("utf-8")).hexdigest()

    assert token == expected_token


@pytest.mark.asyncio
async def test_post_surfaces_status_and_body_when_upstream_returns_non_json(monkeypatch):
    service = TBankService()
    service.password = "secret"
    service.terminal_key = "TERM123"
    service.base_url = "https://rest-api-test.tinkoff.ru/v2"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            text="<html><body>forbidden</body></html>",
            request=request,
        )

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self._client = real_async_client(transport=transport)

        async def __aenter__(self):
            return self._client

        async def __aexit__(self, exc_type, exc, tb):
            await self._client.aclose()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    with pytest.raises(Exception) as exc_info:
        await service._post("AddAccountQr", {"TerminalKey": "TERM123"})

    message = str(exc_info.value)
    assert "Invalid JSON" in message
    assert "status=403" in message
    assert "forbidden" in message
