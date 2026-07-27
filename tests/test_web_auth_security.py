from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Request, Response

from app.api.auth import _enforce_magic_link_rate_limits, _resolve_client_ip
from app.api.dependencies import _request_credential, get_optional_current_user
from app.api.session_helpers import clear_session_cookie, set_session_cookie
from app.core.config import settings


def _request(
    peer: str,
    forwarded: str | None = None,
    *,
    origin: str | None = None,
    cookie: str | None = None,
) -> Request:
    headers = []
    if forwarded:
        headers.append((b"x-forwarded-for", forwarded.encode()))
    if origin:
        headers.append((b"origin", origin.encode()))
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/web/email/request",
            "headers": headers,
            "client": (peer, 1234),
            "server": ("testserver", 443),
            "scheme": "https",
        }
    )


def test_session_cookie_is_http_only_and_can_be_cleared(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_COOKIE_SECURE", True)
    monkeypatch.setattr(settings, "AUTH_COOKIE_SAMESITE", "lax")
    response = Response()

    set_session_cookie(response, "signed-token")
    cookie = response.headers["set-cookie"]
    assert "lightny_session=signed-token" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie

    clear_session_cookie(response)
    assert "Max-Age=0" in response.headers.getlist("set-cookie")[-1]


def test_client_ip_uses_forwarded_chain_only_from_trusted_proxy(monkeypatch):
    monkeypatch.setattr(settings, "WEB_AUTH_TRUSTED_PROXY_CIDRS", ("10.0.0.0/8",))

    assert _resolve_client_ip(_request("10.2.3.4", "198.51.100.9, 10.3.4.5")) == "198.51.100.9"
    assert _resolve_client_ip(_request("203.0.113.7", "198.51.100.9")) is None
    assert _resolve_client_ip(_request("198.51.100.8")) == "198.51.100.8"


def test_cookie_authenticated_mutation_requires_an_allowed_origin(monkeypatch):
    monkeypatch.setattr(settings, "CORS_ALLOWED_ORIGINS", ("https://app.example.com",))
    cookie = f"{settings.AUTH_COOKIE_NAME}=signed-token"

    with pytest.raises(HTTPException) as exc_info:
        _request_credential(_request("198.51.100.8", cookie=cookie), None)
    assert exc_info.value.status_code == 403
    assert _request_credential(
        _request("198.51.100.8", origin="https://app.example.com", cookie=cookie),
        None,
    ) == "signed-token"


@pytest.mark.asyncio
async def test_untrusted_forwarded_header_does_not_create_shared_ip_limit(monkeypatch):
    monkeypatch.setattr(settings, "WEB_AUTH_TRUSTED_PROXY_CIDRS", ())
    redis = AsyncMock()
    redis.incr.side_effect = [1, 1]

    await _enforce_magic_link_rate_limits(
        redis,
        _request("10.2.3.4", "198.51.100.9"),
        "person@example.com",
    )

    assert redis.incr.await_count == 2
    assert all(":ip:" not in call.args[0] for call in redis.incr.await_args_list)


@pytest.mark.asyncio
async def test_optional_auth_rejects_an_invalid_supplied_token():
    with pytest.raises(HTTPException) as exc_info:
        await get_optional_current_user(
            request=_request("198.51.100.8"),
            token="not-a-valid-jwt",
            session=AsyncMock(),
        )

    assert exc_info.value.status_code == 401
