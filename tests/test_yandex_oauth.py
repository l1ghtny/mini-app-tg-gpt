import hashlib
import asyncio
from datetime import timedelta
import os
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api import auth as auth_api, yandex_oauth
from app.api.dependencies import get_redis
from app.api.session_helpers import create_browser_session, resolve_browser_session
from app.core.config import settings
from app.db import models
from app.db.database import get_session
from app.db.subscription_tiers import UserSubscription, SubscriptionTier
from app.db.allowance import AllowanceAccount
from redis.asyncio import Redis


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.counts = {}

    async def set(self, key, value, ex=None):
        self.values[key] = value

    async def getdel(self, key):
        return self.values.pop(key, None)

    async def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key, seconds):
        pass


@pytest.fixture(autouse=True)
def configure(monkeypatch):
    monkeypatch.setattr(settings, "WEB_AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "YANDEX_OAUTH_ENABLED", True)
    monkeypatch.setattr(settings, "YANDEX_OAUTH_CLIENT_ID", "test-client")
    monkeypatch.setattr(settings, "YANDEX_OAUTH_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(settings, "WEBAPP_URL", "https://app.example.com")
    monkeypatch.setattr(
        settings,
        "YANDEX_OAUTH_REDIRECT_URI",
        "https://app.example.com" + yandex_oauth.CALLBACK_PATH,
    )
    monkeypatch.setattr(
        settings, "WEB_AUTH_ADDITIONAL_ORIGINS", ("https://beta.example.com",)
    )
    monkeypatch.setattr(settings, "CORS_ALLOWED_ORIGINS", ("https://app.example.com",))
    monkeypatch.setattr(settings, "AUTH_COOKIE_DOMAIN", None)
    monkeypatch.setattr(settings, "AUTH_COOKIE_SECURE", True)
    monkeypatch.setattr(settings, "STARTER_BUNDLE_NAME", "free")
    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_TRIAL_ENABLED", False)
    monkeypatch.setattr(settings, "DEPLOYMENT_CHANNEL", "production")


def request(cookies="", host="app.example.com"):
    return Request(
        {
            "type": "http",
            "scheme": "https",
            "method": "GET",
            "path": yandex_oauth.CALLBACK_PATH,
            "headers": [(b"host", host.encode()), (b"cookie", cookies.encode())],
            "client": ("127.0.0.1", 1234),
            "server": (host, 443),
        }
    )


async def attempt(redis, *, origin=None, return_to="/", host="app.example.com"):
    response = Response()
    url = await yandex_oauth.begin_login(
        redis, request(host=host), response, origin=origin, return_to=return_to
    )
    query = parse_qs(urlsplit(url).query)
    cookie = SimpleCookie(response.headers["set-cookie"])
    binding = "; ".join(f"{k}={v.value}" for k, v in cookie.items())
    return query, request(binding, host=host)


@pytest.mark.asyncio
async def test_browser_binding_pkce_safe_return_and_replay():
    redis = FakeRedis()
    query, req = await attempt(redis, return_to="//attacker.example/steal")
    assert query["scope"] == ["login:info"]
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["code_challenge"][0]) == 43
    state = query["state"][0]
    saved = await yandex_oauth.consume_attempt(redis, req, state)
    assert saved["return_to"] == "/"
    assert saved["target_user_id"] is None
    assert hashlib.sha256(saved["code_verifier"].encode()).digest()
    with pytest.raises(HTTPException, match="yandex_login_state_invalid"):
        await yandex_oauth.consume_attempt(redis, req, state)


@pytest.mark.asyncio
async def test_stolen_or_expired_state_cannot_exchange_code(monkeypatch):
    redis = FakeRedis()
    query, _ = await attempt(redis)
    exchange = AsyncMock()
    monkeypatch.setattr(yandex_oauth, "fetch_subject", exchange)
    session = AsyncMock()
    response = await auth_api.finish_yandex_login(
        request(), "stolen-code", query["state"][0], None, session, redis
    )
    assert "yandex_login=expired" in response.headers["location"]
    assert "Cache-Control" in response.headers
    exchange.assert_not_awaited()


@pytest.mark.asyncio
async def test_beta_return_origin_and_external_origin_are_explicit():
    redis = FakeRedis()
    query, req = await attempt(
        redis,
        origin="https://beta.example.com",
        host="beta.example.com",
        return_to="/chat/1?entry=test",
    )
    saved = await yandex_oauth.consume_attempt(redis, req, query["state"][0])
    assert query["redirect_uri"] == [
        "https://beta.example.com" + yandex_oauth.CALLBACK_PATH
    ]
    assert (
        yandex_oauth.frontend_redirect(saved, "success")
        == "https://beta.example.com/chat/1?entry=test&yandex_login=success"
    )
    with pytest.raises(HTTPException):
        await attempt(redis, origin="https://attacker.example")
    with pytest.raises(HTTPException, match="yandex_login_origin_mismatch"):
        await attempt(redis, host="api.example.com")
    assert (
        yandex_oauth.callback_return_origin(request(host="beta.example.com"))
        == "https://beta.example.com"
    )

    saved["return_to"] = "/chat/1?yandex_login=success&telegram_login=success&entry=guide"
    result = parse_qs(urlsplit(yandex_oauth.frontend_redirect(saved, "conflict")).query)
    assert result == {"entry": ["guide"], "yandex_login": ["conflict"]}


@pytest.mark.asyncio
async def test_multiple_tabs_have_independent_binding_cookies():
    redis = FakeRedis()
    q1, r1 = await attempt(redis)
    q2, r2 = await attempt(redis)
    assert q1["state"] != q2["state"]
    await yandex_oauth.consume_attempt(redis, r2, q2["state"][0])
    await yandex_oauth.consume_attempt(redis, r1, q1["state"][0])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "profile",
    [
        {"id": "123", "client_id": "wrong"},
        {"client_id": "test-client"},
        {"id": True, "client_id": "test-client"},
        {"id": "0", "client_id": "test-client"},
    ],
)
async def test_provider_subject_and_client_are_validated(monkeypatch, profile):
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            json={"access_token": "secret-token"}
            if req.url.path == "/token"
            else profile,
        )
    )
    monkeypatch.setattr(
        yandex_oauth.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    with pytest.raises(HTTPException, match="yandex_login_unavailable"):
        await yandex_oauth.fetch_subject("code", "verifier")


@pytest.mark.asyncio
async def test_provider_http_clients_ignore_broken_ai_proxy(monkeypatch):
    real_client = httpx.AsyncClient
    for env in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.setenv(env, "socks5://unreachable.invalid:1080")
    clients = []
    requests = []

    def respond(req):
        requests.append(req)
        return httpx.Response(
            200,
            json={"access_token": "secret-token"}
            if req.url.path == "/token"
            else {
                "id": "123",
                "client_id": "test-client",
                "default_email": "ignored@example.com",
            },
        )

    def factory(**kwargs):
        clients.append(kwargs)
        return real_client(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(yandex_oauth.httpx, "AsyncClient", factory)
    assert await yandex_oauth.fetch_subject("code", "verifier") == "123"
    assert all(c["trust_env"] is False for c in clients)
    assert "code_verifier=verifier" in requests[0].content.decode()
    assert requests[1].headers["Authorization"] == "OAuth secret-token"
    assert "secret-token" not in str(requests[1].url)


def db_url():
    value = os.getenv("TEST_DATABASE_URL")
    if not value:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration")
    return value


@pytest.mark.asyncio
async def test_signup_repeat_login_links_and_conflicts_preserve_canonical_account():
    engine = create_async_engine(db_url())
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = await yandex_oauth.resolve_identity(session, "111")
        user_id = user.id
        history = (
            await session.exec(
                select(UserSubscription).where(UserSubscription.user_id == user.id)
            )
        ).all()
        assert len(history) == 1
        repeated = await yandex_oauth.resolve_identity(session, "111")
        assert repeated.id == user_id
        other = models.AppUser(telegram_id=None)
        session.add(other)
        await session.commit()
        with pytest.raises(HTTPException, match="account_merge_required"):
            await yandex_oauth.resolve_identity(session, "111", other.id)
        await session.rollback()
        user = await session.get(models.AppUser, user_id)
        await yandex_oauth.resolve_identity(session, "111", user_id)
        after = (
            await session.exec(
                select(UserSubscription).where(UserSubscription.user_id == user_id)
            )
        ).all()
        assert [h.id for h in after] == [h.id for h in history]
        identities = (await session.exec(select(models.UserIdentity))).all()
        assert len(identities) == 1 and identities[0].user_id == user_id
        assert identities[0].email is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_existing_account_link_does_not_issue_a_new_trial(monkeypatch):
    engine = create_async_engine(db_url())
    grant = AsyncMock()
    monkeypatch.setattr(yandex_oauth, "ensure_starter_bundle", grant)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = models.AppUser(telegram_id=799123001)
        session.add(user)
        await session.commit()
        original_id = user.id
        linked = await yandex_oauth.resolve_identity(session, "222", original_id)
        assert linked.id == original_id and linked.telegram_id == 799123001
        grant.assert_not_awaited()
        with pytest.raises(HTTPException, match="yandex_already_linked"):
            await yandex_oauth.resolve_identity(session, "333", original_id)
        await session.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_last_method_protection_counts_passkeys_and_serializes_removal():
    engine = create_async_engine(db_url())
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = await yandex_oauth.resolve_identity(session, "444")
        with pytest.raises(HTTPException, match="last_identity_cannot_be_removed"):
            await auth_api.unlink_identity("yandex", user, session)
        await session.rollback()
        user = (await session.exec(select(models.AppUser))).one()
        key = models.PasskeyCredential(
            user_id=user.id,
            credential_id="test-key",
            public_key=b"test-public-key",
            sign_count=0,
            name="Test",
        )
        session.add(key)
        await session.commit()
        await auth_api.unlink_identity("yandex", user, session)
        with pytest.raises(HTTPException, match="last_identity_cannot_be_removed"):
            await auth_api.delete_passkey(key.id, user, session)
        await session.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_http_login_session_rotation_recovery_and_link_after_logout(monkeypatch):
    engine = create_async_engine(db_url())
    redis = FakeRedis()
    app = FastAPI()
    app.include_router(auth_api.auth, prefix="/api/v1")

    async def sessions():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = sessions
    app.dependency_overrides[get_redis] = lambda: redis
    monkeypatch.setattr(yandex_oauth, "fetch_subject", AsyncMock(return_value="555"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="https://app.example.com",
        follow_redirects=False,
    ) as client:
        first = await client.get(
            "/api/v1/auth/yandex/start", params={"return_to": "/chat/1?source=test"}
        )
        state = parse_qs(urlsplit(first.headers["location"]).query)["state"][0]
        done = await client.get(
            yandex_oauth.CALLBACK_PATH, params={"state": state, "code": "test-code"}
        )
        assert (
            done.status_code == 302
            and "yandex_login=success" in done.headers["location"]
        )
        assert "HttpOnly" in " ".join(done.headers.get_list("set-cookie"))
        old_token = client.cookies[settings.AUTH_COOKIE_NAME]
        me = await client.get("/api/v1/auth/me")
        assert me.status_code == 200 and me.json()["auth_providers"] == ["yandex"]
        user_id = me.json()["id"]
        # Reload/recovery is a fresh authenticated API call, with no bearer token.
        assert (await client.get("/api/v1/auth/me")).json()["id"] == user_id
        start = await client.get("/api/v1/auth/yandex/start")
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        await client.get(
            yandex_oauth.CALLBACK_PATH, params={"state": state, "code": "test-code"}
        )
        assert client.cookies[settings.AUTH_COOKIE_NAME] != old_token
        async with AsyncSession(engine) as session:
            assert await resolve_browser_session(session, old_token) is None
        link = await client.post(
            "/api/v1/auth/identities/yandex/link",
            json={"return_to": "/"},
            headers={"Origin": "https://app.example.com"},
        )
        assert link.status_code == 200
        state = parse_qs(urlsplit(link.json()["authorization_url"]).query)["state"][0]
        assert (await client.post("/api/v1/auth/logout")).status_code == 204
        failed = await client.get(
            yandex_oauth.CALLBACK_PATH, params={"state": state, "code": "test-code"}
        )
        assert "yandex_login=session_changed" in failed.headers["location"]
        assert settings.AUTH_COOKIE_NAME not in client.cookies
        assert (await client.get("/api/v1/auth/me")).status_code == 401
    await engine.dispose()


@pytest.mark.asyncio
async def test_cancelled_callback_consumes_state_without_provider_call(monkeypatch):
    redis = FakeRedis()
    query, req = await attempt(redis)
    fetch = AsyncMock()
    monkeypatch.setattr(yandex_oauth, "fetch_subject", fetch)
    response = await auth_api.finish_yandex_login(
        req, None, query["state"][0], "access_denied", AsyncMock(), redis
    )
    assert "yandex_login=cancelled" in response.headers["location"]
    assert not redis.values
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_parallel_first_logins_create_one_account_and_one_starter():
    engine = create_async_engine(db_url())

    async def login():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            return (await yandex_oauth.resolve_identity(session, "777")).id

    ids = await asyncio.gather(login(), login())
    assert ids[0] == ids[1]
    async with AsyncSession(engine) as session:
        assert len((await session.exec(select(models.AppUser))).all()) == 1
        assert len((await session.exec(select(UserSubscription))).all()) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_parallel_unlinks_cannot_remove_both_remaining_methods():
    engine = create_async_engine(db_url())
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = await yandex_oauth.resolve_identity(session, "888")
        user_id = user.id
        session.add(
            models.UserIdentity(
                user_id=user_id,
                provider="email",
                subject="owned@example.com",
                email="owned@example.com",
            )
        )
        await session.commit()

    async def remove(provider):
        async with AsyncSession(engine, expire_on_commit=False) as session:
            user = await session.get(models.AppUser, user_id)
            try:
                await auth_api.unlink_identity(provider, user, session)
                return 204
            except HTTPException as exc:
                await session.rollback()
                return exc.status_code

    assert sorted(await asyncio.gather(remove("email"), remove("yandex"))) == [204, 409]
    async with AsyncSession(engine) as session:
        assert len((await session.exec(select(models.UserIdentity))).all()) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_link_and_repeat_login_preserve_paid_history_and_consumed_trial():
    engine = create_async_engine(db_url())
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = models.AppUser(telegram_id=None)
        session.add(user)
        await session.flush()
        user_id = user.id
        session.add(
            models.UserIdentity(
                user_id=user_id,
                provider="email",
                subject="owned@example.com",
                email="owned@example.com",
            )
        )
        now = models.utcnow_naive()
        trial = AllowanceAccount(
            user_id=user_id,
            scope="test",
            plan="starter",
            period_start=now - timedelta(days=30),
            period_end=now - timedelta(days=16),
            trial_started_at=now - timedelta(days=30),
            rate_version="test",
            granted=100,
            spent=100,
            luna_granted=100,
            luna_spent=100,
        )
        chat = models.Conversation(user_id=user_id, title="Existing history")
        tier = (
            await session.exec(
                select(SubscriptionTier).where(SubscriptionTier.name == "advanced")
            )
        ).one()
        paid_subscription = UserSubscription(
            user_id=user_id,
            tier_id=tier.id,
            started_at=now,
            expires_at=now + timedelta(days=30),
        )
        session.add(paid_subscription)
        await session.flush()
        payment = models.Payment(
            subscription_id=paid_subscription.id,
            user_id=user_id,
            tier_name="advanced",
            amount=99000,
            tbank_status="CONFIRMED",
            confirmation_applied=True,
            confirmed_at=now,
            subscription_period_start=now,
            subscription_period_end=now + timedelta(days=30),
        )
        session.add_all([trial, chat, payment])
        await session.commit()
        before = (
            trial.model_dump(),
            chat.model_dump(),
            payment.model_dump(),
            paid_subscription.model_dump(),
        )
        await yandex_oauth.resolve_identity(session, "999", user_id)
        assert (await yandex_oauth.resolve_identity(session, "999")).id == user_id
        for row in (trial, chat, payment, paid_subscription):
            await session.refresh(row)
        assert (
            trial.model_dump(),
            chat.model_dump(),
            payment.model_dump(),
            paid_subscription.model_dump(),
        ) == before
        assert len((await session.exec(select(models.AppUser))).all()) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_revocation_during_provider_exchange_prevents_link(monkeypatch):
    engine = create_async_engine(db_url())
    redis = FakeRedis()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = models.AppUser(telegram_id=None)
        session.add(user)
        await session.commit()
        token = await create_browser_session(session, user)
        req = request(f"{settings.AUTH_COOKIE_NAME}={token}")
        response = Response()
        url = await yandex_oauth.begin_login(
            redis, req, response, origin=None, return_to="/", user=user, session=session
        )
        binding = SimpleCookie(response.headers["set-cookie"])
        req = request(
            f"{settings.AUTH_COOKIE_NAME}={token}; "
            + "; ".join(f"{k}={v.value}" for k, v in binding.items())
        )
        state = parse_qs(urlsplit(url).query)["state"][0]

        async def exchange(*_):
            from app.api.session_helpers import revoke_browser_session

            async with AsyncSession(engine) as other_session:
                await revoke_browser_session(other_session, token)
            return "1010"

        monkeypatch.setattr(yandex_oauth, "fetch_subject", exchange)
        result = await auth_api.finish_yandex_login(
            req, "code", state, None, session, redis
        )
        assert "yandex_login=session_changed" in result.headers["location"]
        assert not (await session.exec(select(models.UserIdentity))).all()
    await engine.dispose()


@pytest.mark.asyncio
async def test_real_redis_expiry_rejects_a_browser_bound_attempt(monkeypatch):
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL is required for real Redis expiry acceptance")
    redis = Redis.from_url(url, decode_responses=True)
    monkeypatch.setattr(settings, "YANDEX_OAUTH_STATE_TTL_SECONDS", 1)
    try:
        query, req = await attempt(redis)
        state = query["state"][0]
        assert await redis.ttl(yandex_oauth.STATE_PREFIX + state) in (0, 1)
        await asyncio.sleep(1.1)
        with pytest.raises(HTTPException, match="yandex_login_state_invalid"):
            await yandex_oauth.consume_attempt(redis, req, state)
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_retired_rp_passkey_cannot_justify_removing_last_identity(monkeypatch):
    monkeypatch.setattr(settings, "PASSKEY_RP_ID", "example.com")
    engine = create_async_engine(db_url())
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = await yandex_oauth.resolve_identity(session, "121212")
        old = models.PasskeyCredential(
            user_id=user.id,
            credential_id="old-key",
            public_key=b"test",
            rp_id="retired.example.net",
        )
        session.add(old)
        await session.commit()
        profile = await auth_api.build_user_profile(session, user)
        assert profile["passkey_count"] == 0
        with pytest.raises(HTTPException, match="last_identity_cannot_be_removed"):
            await auth_api.unlink_identity("yandex", user, session)
        await session.rollback()
        user = (await session.exec(select(models.AppUser))).one()
        current = models.PasskeyCredential(
            user_id=user.id,
            credential_id="current-key",
            public_key=b"test",
            rp_id="example.com",
        )
        session.add(current)
        await session.commit()
        old = (
            await session.exec(
                select(models.PasskeyCredential).where(
                    models.PasskeyCredential.credential_id == "old-key"
                )
            )
        ).one()
        await auth_api.unlink_identity("yandex", user, session)
        await auth_api.delete_passkey(old.id, user, session)
        with pytest.raises(HTTPException, match="last_identity_cannot_be_removed"):
            await auth_api.delete_passkey(current.id, user, session)
        await session.rollback()
    await engine.dispose()
