from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Request, Response

import app.api.auth as auth_api
from app.api.auth import (
    MagicLinkRequest,
    _enforce_magic_link_rate_limits,
    _resolve_client_ip,
)
from app.api.dependencies import _request_credential, get_optional_current_user
from app.api.session_helpers import clear_session_cookie, set_session_cookie
from app.core.config import settings
from app.db.models import AppUser


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

    assert (
        _resolve_client_ip(_request("10.2.3.4", "198.51.100.9, 10.3.4.5"))
        == "198.51.100.9"
    )
    assert _resolve_client_ip(_request("203.0.113.7", "198.51.100.9")) is None
    assert _resolve_client_ip(_request("198.51.100.8")) == "198.51.100.8"


def test_cookie_authenticated_mutation_requires_an_allowed_origin(monkeypatch):
    monkeypatch.setattr(settings, "CORS_ALLOWED_ORIGINS", ("https://app.example.com",))
    cookie = f"{settings.AUTH_COOKIE_NAME}=signed-token"

    with pytest.raises(HTTPException) as exc_info:
        _request_credential(_request("198.51.100.8", cookie=cookie), None)
    assert exc_info.value.status_code == 403
    assert (
        _request_credential(
            _request("198.51.100.8", origin="https://app.example.com", cookie=cookie),
            None,
        )
        == "signed-token"
    )


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


@pytest.mark.asyncio
async def test_explicit_email_routes_do_not_infer_login_or_link_intent(monkeypatch):
    monkeypatch.setattr(settings, "WEB_AUTH_ENABLED", True)
    rate_limit = AsyncMock()
    issue_link = AsyncMock(return_value="debug-token")
    monkeypatch.setattr(auth_api, "_enforce_magic_link_rate_limits", rate_limit)
    monkeypatch.setattr(auth_api, "issue_magic_link", issue_link)

    session = AsyncMock()
    redis = AsyncMock()
    payload = MagicLinkRequest(email="  Person@Example.COM ")
    request = _request("198.51.100.8")

    login_result = await auth_api.request_email_login_link(
        payload, request, session, redis
    )

    assert login_result.debug_token == "debug-token"
    issue_link.assert_awaited_once_with(
        session,
        email="person@example.com",
        target_user=None,
        debug_delivery=False,
    )

    issue_link.reset_mock()
    current_user = AppUser(telegram_id=799000003)
    link_result = await auth_api.request_email_identity_link(
        payload,
        request,
        session,
        redis,
        current_user,
    )

    assert link_result.debug_token == "debug-token"
    issue_link.assert_awaited_once_with(
        session,
        email="person@example.com",
        target_user=current_user,
        debug_delivery=False,
    )


def test_debug_magic_link_is_only_exposed_to_loopback_browser(monkeypatch):
    monkeypatch.setattr(settings, "TEST_ENV", False)
    monkeypatch.setattr(settings, "DEBUG_MODE", True)

    assert (
        auth_api._allow_debug_magic_link(
            _request("127.0.0.1", origin="http://127.0.0.1:4175")
        )
        is True
    )
    assert (
        auth_api._allow_debug_magic_link(
            _request("192.168.100.25", origin="http://192.168.100.49:4175")
        )
        is False
    )
    assert (
        auth_api._allow_debug_magic_link(
            _request("127.0.0.1", origin="https://app.lightny.ru")
        )
        is False
    )


def test_local_debug_auth_requires_loopback_peer_and_origin(monkeypatch):
    monkeypatch.setattr(settings, "TEST_ENV", False)
    monkeypatch.setattr(settings, "DEBUG_MODE", True)

    assert (
        auth_api._allow_local_debug_request(
            _request("127.0.0.1", origin="http://localhost:4175")
        )
        is True
    )
    assert (
        auth_api._allow_local_debug_request(
            _request("192.168.100.25", origin="http://localhost:4175")
        )
        is False
    )
    assert (
        auth_api._allow_local_debug_request(
            _request("127.0.0.1", origin="http://192.168.100.25:4175")
        )
        is False
    )
