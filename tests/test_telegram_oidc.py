from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException, Request

import app.api.auth as auth_api
import app.api.telegram_oidc as telegram_oidc
from app.api.telegram_oidc import TelegramOidcIdentity
from app.core.config import settings
from app.db.models import AppUser


class FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}

    async def set(self, key: str, value: str, **_kwargs):
        self.values[key] = value
        return True

    async def get(self, key: str):
        return self.values.get(key)

    async def getdel(self, key: str):
        return self.values.pop(key, None)


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/auth/telegram/oidc/callback",
            "headers": [],
            "client": ("198.51.100.10", 443),
        }
    )


def _configure(monkeypatch):
    monkeypatch.setattr(settings, "WEB_AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "TELEGRAM_OIDC_ENABLED", True)
    monkeypatch.setattr(settings, "TELEGRAM_OIDC_CLIENT_ID", "123456")
    monkeypatch.setattr(settings, "TELEGRAM_OIDC_CLIENT_SECRET", "secret")
    monkeypatch.setattr(
        settings,
        "TELEGRAM_OIDC_REDIRECT_URI",
        "https://api.lightny.ru/api/v1/auth/telegram/oidc/callback",
    )
    monkeypatch.setattr(settings, "WEBAPP_URL", "https://app.lightny.ru")
    monkeypatch.setattr(settings, "TELEGRAM_OIDC_STATE_TTL_SECONDS", 600)


@pytest.mark.asyncio
async def test_oidc_start_uses_pkce_and_rejects_external_return_url(monkeypatch):
    _configure(monkeypatch)
    redis = FakeRedis()

    authorization_url = await telegram_oidc.begin_telegram_oidc(
        redis,
        return_to="https://attacker.example/steal",
    )

    query = parse_qs(urlsplit(authorization_url).query)
    assert query["client_id"] == ["123456"]
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["scope"] == ["openid profile telegram:bot_access"]
    assert len(query["code_challenge"][0]) >= 43
    saved = next(iter(redis.values.values()))
    assert '"return_to":"/"' in saved


@pytest.mark.asyncio
async def test_oidc_completion_consumes_state_once_and_uses_verified_profile(
    monkeypatch,
):
    _configure(monkeypatch)
    redis = FakeRedis()
    authorization_url = await telegram_oidc.begin_telegram_oidc(
        redis, return_to="/chat/abc?from=test"
    )
    state = parse_qs(urlsplit(authorization_url).query)["state"][0]
    exchange = AsyncMock(return_value="signed-id-token")
    verify = AsyncMock(
        return_value={
            "id": 799100200,
            "preferred_username": "friend",
            "given_name": "Pilot",
            "family_name": "Tester",
            "picture": "https://cdn.example/pilot.jpg",
        }
    )
    monkeypatch.setattr(telegram_oidc, "_exchange_code", exchange)
    monkeypatch.setattr(telegram_oidc, "_verify_id_token", verify)

    identity = await telegram_oidc.complete_telegram_oidc(
        redis, code="code", state=state
    )

    assert identity.telegram_id == 799100200
    assert identity.return_to == "/chat/abc?from=test"
    assert identity.profile == {
        "username": "friend",
        "first_name": "Pilot",
        "last_name": "Tester",
        "photo_url": "https://cdn.example/pilot.jpg",
    }
    exchange.assert_awaited_once()
    verify.assert_awaited_once()
    with pytest.raises(HTTPException) as replay:
        await telegram_oidc.complete_telegram_oidc(redis, code="code", state=state)
    assert replay.value.status_code == 400


@pytest.mark.asyncio
async def test_oidc_token_verification_pins_issuer_audience_key_and_nonce(monkeypatch):
    _configure(monkeypatch)
    redis = FakeRedis()
    monkeypatch.setattr(
        telegram_oidc,
        "_load_jwks",
        AsyncMock(return_value={"keys": [{"kid": "key-1", "alg": "RS256"}]}),
    )
    monkeypatch.setattr(
        telegram_oidc.jwt,
        "get_unverified_header",
        lambda _token: {"kid": "key-1", "alg": "RS256"},
    )
    decode = Mock(return_value={"id": 799100200, "nonce": "expected-nonce"})
    monkeypatch.setattr(telegram_oidc.jwt, "decode", decode)

    claims = await telegram_oidc._verify_id_token(
        redis,
        "signed-token",
        "expected-nonce",
    )

    assert claims["id"] == 799100200
    assert decode.call_args.kwargs == {
        "algorithms": ["RS256"],
        "audience": "123456",
        "issuer": "https://oauth.telegram.org",
    }


@pytest.mark.asyncio
async def test_oidc_callback_sets_browser_session_for_resolved_telegram_user(
    monkeypatch,
):
    _configure(monkeypatch)
    user = AppUser(telegram_id=799100201, telegram_first_name="Pilot")
    resolved = TelegramOidcIdentity(
        telegram_id=799100201,
        profile={
            "username": "friend",
            "first_name": "Pilot",
            "last_name": None,
            "photo_url": "https://cdn.example/pilot.jpg",
        },
        return_to="/",
    )
    monkeypatch.setattr(
        auth_api, "complete_telegram_oidc", AsyncMock(return_value=resolved)
    )
    login = AsyncMock(return_value=("bearer", False))
    monkeypatch.setattr(auth_api, "process_login", login)
    monkeypatch.setattr(
        auth_api, "create_browser_session", AsyncMock(return_value="browser-session")
    )
    monkeypatch.setattr(settings, "AUTH_COOKIE_SECURE", True)

    result = AsyncMock()
    result.first.return_value = user
    session = AsyncMock()
    session.exec.return_value = result

    response = await auth_api.finish_telegram_oidc_login(
        request=_request(),
        code="code",
        state="state",
        session=session,
        redis=FakeRedis(),
    )

    assert response.status_code == 302
    assert (
        response.headers["location"] == "https://app.lightny.ru/?telegram_login=success"
    )
    assert "lightny_session=browser-session" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    login.assert_awaited_once_with(
        session,
        799100201,
        telegram_profile=resolved.profile,
    )
