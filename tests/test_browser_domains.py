import json
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException, Request

from app.api import telegram_oidc, passkey_helpers, web_auth_helpers
from app.api.browser_origins import resolve_browser_origin
from app.core.config import settings
from app.db.models import WebAuthChallenge

OLD = "https://app.lightny.ru"
NEW = "https://app.lightnyai.ru"

@pytest.fixture(autouse=True)
def domains(monkeypatch):
    monkeypatch.setattr(settings, "WEBAPP_URL", OLD)
    monkeypatch.setattr(settings, "WEB_AUTH_ADDITIONAL_ORIGINS", (NEW,))
    monkeypatch.setattr(settings, "PASSKEY_ALLOWED_ORIGINS", (OLD, NEW))
    monkeypatch.setattr(settings, "PASSKEY_RP_ID", "app.lightny.ru")
    monkeypatch.setattr(settings, "WEB_AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "TELEGRAM_OIDC_ENABLED", True)
    monkeypatch.setattr(settings, "TELEGRAM_OIDC_CLIENT_ID", "test")
    monkeypatch.setattr(settings, "TELEGRAM_OIDC_CLIENT_SECRET", "test")
    monkeypatch.setattr(settings, "TELEGRAM_OIDC_REDIRECT_URI", OLD + "/api/v1/auth/telegram/oidc/callback")

class Redis:
    def __init__(self): self.values = {}
    async def set(self, key, value, **kwargs): self.values[key] = value; return True
    async def get(self, key): return self.values.get(key)
    async def getdel(self, key): return self.values.pop(key, None)

@pytest.mark.parametrize("origin", ["https://evil.example", NEW + ".evil.example", NEW + "/path", "http://app.lightnyai.ru", "null"])
def test_origin_rejects_unregistered_destinations(origin):
    with pytest.raises(HTTPException): resolve_browser_origin(origin)

@pytest.mark.parametrize("origin,rp", [(OLD,"app.lightny.ru"), (NEW,"app.lightnyai.ru")])
def test_passkey_uses_domain_specific_rp(origin,rp):
    request = Request({"type":"http", "headers":[(b"origin", origin.encode())]})
    assert passkey_helpers.resolve_passkey_context(request) == (origin,rp)

@pytest.mark.asyncio
async def test_new_oidc_binds_redirect_through_exchange_and_replay(monkeypatch):
    redis = Redis()
    url = await telegram_oidc.begin_telegram_oidc(redis, origin=NEW, return_to="/chat/abc")
    query = parse_qs(urlsplit(url).query)
    callback = NEW + "/api/v1/auth/telegram/oidc/callback"
    assert query["redirect_uri"] == [callback]
    state = query["state"][0]
    saved = json.loads(next(iter(redis.values.values())))
    exchange = AsyncMock(return_value="token")
    monkeypatch.setattr(telegram_oidc, "_exchange_code", exchange)
    monkeypatch.setattr(telegram_oidc, "_verify_id_token", AsyncMock(return_value={"id":123}))
    identity = await telegram_oidc.complete_telegram_oidc(redis, code="code", state=state)
    exchange.assert_awaited_once_with("code", saved["code_verifier"], callback)
    assert telegram_oidc.frontend_redirect(identity.return_to, "success", identity.frontend_origin) == NEW + "/chat/abc?telegram_login=success"
    with pytest.raises(HTTPException): await telegram_oidc.complete_telegram_oidc(redis, code="code", state=state)

@pytest.mark.asyncio
async def test_cancel_returns_to_starting_origin_and_consumes_state():
    redis = Redis()
    url = await telegram_oidc.begin_telegram_oidc(redis, origin=NEW)
    state = parse_qs(urlsplit(url).query)["state"][0]
    assert await telegram_oidc.oidc_return_origin(redis, state, consume=True) == NEW
    assert not redis.values
    assert await telegram_oidc.oidc_return_origin(redis, state) == OLD

@pytest.mark.asyncio
async def test_wrong_origin_passkey_response_is_rejected_before_verification():
    redis = Redis()
    ceremony, _ = await passkey_helpers.begin_passkey_authentication(redis, origin=OLD, rp_id="app.lightny.ru")
    session = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await passkey_helpers.finish_passkey_authentication(session, redis, ceremony_id=ceremony, credential={}, origin=NEW)
    assert exc.value.detail == "passkey_origin_mismatch"
    session.exec.assert_not_awaited()

def test_email_callback_is_new_domain_and_uses_fragment(monkeypatch):
    monkeypatch.setattr(settings,"WEB_AUTH_CALLBACK_URL",OLD + "/auth/callback")
    assert web_auth_helpers._callback_url("opaque-token", NEW) == NEW + "/auth/callback#token=opaque-token"
    assert web_auth_helpers._callback_url("opaque-token", OLD) == OLD + "/auth/callback#token=opaque-token"

@pytest.mark.asyncio
async def test_wrong_domain_cannot_consume_email_challenge():
    challenge = WebAuthChallenge(browser_origin=NEW, email="test@example.com")
    rows = Mock(); rows.first.return_value = challenge
    session = Mock(); session.exec = AsyncMock(return_value=rows); session.commit=AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await web_auth_helpers.consume_magic_link(session, token="opaque", origin=OLD)
    assert exc.value.detail == "web_login_origin_mismatch"
    session.commit.assert_not_awaited()

@pytest.mark.asyncio
async def test_email_issuance_persists_origin():
    session = Mock(); session.commit=AsyncMock()
    token = await web_auth_helpers.issue_magic_link(session, email="test@example.com", target_user=None, debug_delivery=True, origin=NEW)
    assert token
    assert session.add.call_args.args[0].browser_origin == NEW
