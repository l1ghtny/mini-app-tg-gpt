"""Yandex code + PKCE login. Provider tokens never leave this request."""

import base64
import hashlib
import json
import secrets
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import HTTPException, Request, Response
from redis.asyncio import Redis
from sqlalchemy import text
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.auth_helpers import ensure_starter_bundle
from app.api.browser_origins import resolve_browser_origin
from app.api.session_helpers import resolve_browser_session
from app.api.telegram_oidc import normalize_return_to
from app.core.config import settings
from app.core.deployment_channel import ensure_deployment_user_allowed, is_beta_channel
from app.db import models

CALLBACK_PATH = "/api/v1/auth/yandex/callback"
COOKIE_PATH = "/api/v1/auth"
STATE_PREFIX = "yandex:oauth:state:"
AUTHORIZE_URL = "https://oauth.yandex.ru/authorize"
TOKEN_URL = "https://oauth.yandex.ru/token"
USERINFO_URL = "https://login.yandex.ru/info"


def require_config() -> None:
    if not settings.WEB_AUTH_ENABLED or not settings.YANDEX_OAUTH_ENABLED:
        raise HTTPException(404, "Not Found")
    if (
        not settings.YANDEX_OAUTH_CLIENT_ID
        or not settings.YANDEX_OAUTH_CLIENT_SECRET
        or not settings.YANDEX_OAUTH_REDIRECT_URI
    ):
        raise HTTPException(503, "yandex_login_not_configured")


def callback_url(origin: str) -> str:
    origin = resolve_browser_origin(origin)
    if origin == (settings.WEBAPP_URL or "").rstrip("/"):
        callback = settings.YANDEX_OAUTH_REDIRECT_URI
    else:
        callback = f"{origin}{CALLBACK_PATH}"
    if callback != f"{origin}{CALLBACK_PATH}":
        raise HTTPException(503, "yandex_login_not_configured")
    return callback


def binding_cookie_name(state: str) -> str:
    return "lightny_yandex_" + hashlib.sha256(state.encode()).hexdigest()[:24]


def callback_return_origin(request: Request) -> str:
    # Expired state has no destination left. Recover the exact allowlisted
    # callback host without trusting forwarded headers or an arbitrary URL.
    for origin in ((settings.WEBAPP_URL or ""), *settings.WEB_AUTH_ADDITIONAL_ORIGINS):
        if urlsplit(origin).netloc == request.url.netloc:
            return resolve_browser_origin(origin)
    return resolve_browser_origin()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def begin_login(
    redis: Redis,
    request: Request,
    response: Response,
    *,
    origin: str | None,
    return_to: str | None,
    user: models.AppUser | None = None,
    session: AsyncSession | None = None,
) -> str:
    require_config()
    frontend_origin = resolve_browser_origin(origin)
    redirect_uri = callback_url(frontend_origin)
    # A host-only binding cookie must be received on the callback host as well.
    if request.url.hostname != urlsplit(redirect_uri).hostname:
        raise HTTPException(400, "yandex_login_origin_mismatch")
    session_hash = None
    if user is not None:
        if is_beta_channel() and not settings.BETA_ALLOW_IDENTITY_MUTATIONS:
            raise HTTPException(403, "beta_identity_mutation_disabled")
        cookie = request.cookies.get(settings.AUTH_COOKIE_NAME)
        resolved = (
            await resolve_browser_session(session, cookie)
            if cookie and session
            else None
        )
        if not resolved or resolved[0].id != user.id:
            raise HTTPException(401, "yandex_link_session_required")
        session_hash = _hash(cookie)
    state = secrets.token_urlsafe(32)
    binding = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    payload = {
        "binding_hash": _hash(binding),
        "code_verifier": verifier,
        "frontend_origin": frontend_origin,
        "redirect_uri": redirect_uri,
        "return_to": normalize_return_to(return_to),
        "target_user_id": str(user.id) if user else None,
        "session_hash": session_hash,
    }
    await redis.set(
        STATE_PREFIX + state,
        json.dumps(payload),
        ex=settings.YANDEX_OAUTH_STATE_TTL_SECONDS,
    )
    response.set_cookie(
        binding_cookie_name(state),
        binding,
        max_age=settings.YANDEX_OAUTH_STATE_TTL_SECONDS,
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite="lax",
        path=COOKIE_PATH,
    )
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return (
        AUTHORIZE_URL
        + "?"
        + urlencode(
            {
                "client_id": settings.YANDEX_OAUTH_CLIENT_ID,
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "scope": "login:info",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "force_confirm": "yes",
            }
        )
    )


async def consume_attempt(redis: Redis, request: Request, state: str | None) -> dict:
    require_config()
    raw = (
        await redis.getdel(STATE_PREFIX + state)
        if state and len(state) <= 256
        else None
    )
    try:
        saved = json.loads(raw) if raw else None
        binding = request.cookies.get(binding_cookie_name(state)) if state else None
        if (
            not isinstance(saved, dict)
            or not binding
            or not secrets.compare_digest(saved["binding_hash"], _hash(binding))
        ):
            raise ValueError()
        origin = resolve_browser_origin(saved["frontend_origin"])
        if saved["redirect_uri"] != callback_url(origin):
            raise ValueError()
        if not isinstance(saved["code_verifier"], str) or not isinstance(
            saved["return_to"], str
        ):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise HTTPException(400, "yandex_login_state_invalid") from None
    return saved


async def fetch_subject(code: str, verifier: str) -> str:
    try:
        async with httpx.AsyncClient(
            timeout=settings.YANDEX_OAUTH_HTTP_TIMEOUT_SECONDS, trust_env=False
        ) as client:
            token_response = await client.post(
                TOKEN_URL,
                auth=httpx.BasicAuth(
                    settings.YANDEX_OAUTH_CLIENT_ID, settings.YANDEX_OAUTH_CLIENT_SECRET
                ),
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": verifier,
                },
            )
            token_response.raise_for_status()
            token = token_response.json().get("access_token")
            if not isinstance(token, str) or not token:
                raise ValueError()
            profile_response = await client.get(
                USERINFO_URL,
                params={"format": "json"},
                headers={"Authorization": f"OAuth {token}"},
            )
            profile_response.raise_for_status()
            profile = profile_response.json()
            subject = profile.get("id")
            if (
                profile.get("client_id") != settings.YANDEX_OAUTH_CLIENT_ID
                or not isinstance(subject, str)
                or not subject.isascii()
                or not subject.isdecimal()
                or int(subject) <= 0
                or len(subject) > 64
            ):
                raise ValueError()
    except (httpx.HTTPError, ValueError, AttributeError, TypeError):
        raise HTTPException(502, "yandex_login_unavailable") from None
    return subject


async def resolve_identity(
    session: AsyncSession,
    subject: str,
    target_user_id: uuid.UUID | None = None,
    link_session_hash: str | None = None,
) -> models.AppUser:
    # Serializes first sign-in and conflicting links across workers; uniqueness
    # alone would leave an orphan account when two callbacks arrive together.
    await session.exec(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
        params={"identity": f"yandex:{subject}"},
    )
    identity = (
        await session.exec(
            select(models.UserIdentity).where(
                models.UserIdentity.provider == "yandex",
                models.UserIdentity.subject == subject,
            )
        )
    ).first()
    if target_user_id and identity and identity.user_id != target_user_id:
        raise HTTPException(409, "account_merge_required")
    user_id = target_user_id or (identity.user_id if identity else None)
    if user_id:
        user = (
            await session.exec(
                select(models.AppUser)
                .where(models.AppUser.id == user_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).first()
        if not user or user.deleted_at is not None:
            raise HTTPException(403, "login_account_unavailable")
        ensure_deployment_user_allowed(user)
    else:
        ensure_deployment_user_allowed(None)
        user = models.AppUser(telegram_id=None)
        session.add(user)
        await session.flush()
    if identity is not None:
        still_linked = (
            await session.exec(
                select(models.UserIdentity).where(models.UserIdentity.id == identity.id)
            )
        ).first()
        if still_linked is None:
            raise HTTPException(400, "yandex_login_identity_changed")
    if target_user_id and link_session_hash:
        browser_session = (
            await session.exec(
                select(models.BrowserSession)
                .where(
                    models.BrowserSession.token_hash == link_session_hash,
                    models.BrowserSession.user_id == user.id,
                    models.BrowserSession.revoked_at.is_(None),
                    models.BrowserSession.expires_at > models.utcnow_naive(),
                )
                .with_for_update()
            )
        ).first()
        if not browser_session:
            raise HTTPException(403, "yandex_link_session_changed")
    if target_user_id:
        linked = (
            await session.exec(
                select(models.UserIdentity).where(
                    models.UserIdentity.user_id == user.id,
                    models.UserIdentity.provider == "yandex",
                )
            )
        ).first()
        if linked and linked.subject != subject:
            raise HTTPException(409, "yandex_already_linked")
    if identity is None:
        session.add(
            models.UserIdentity(user_id=user.id, provider="yandex", subject=subject)
        )
    else:
        identity.last_used_at = models.utcnow_naive()
        session.add(identity)
    await session.commit()
    if target_user_id is None:
        # The starter helper may commit; hold the canonical account lock while
        # it reads claim/history state so concurrent logins cannot grant twice.
        await session.exec(
            select(models.AppUser).where(models.AppUser.id == user.id).with_for_update()
        )
        await ensure_starter_bundle(session, user)
        await session.commit()
    return user


def frontend_redirect(saved: dict | None, result: str) -> str:
    origin = resolve_browser_origin(saved.get("frontend_origin") if saved else None)
    return_to = normalize_return_to(saved.get("return_to") if saved else "/")
    parts = urlsplit(return_to)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key not in {"yandex_login", "telegram_login"}
    ]
    query.append(("yandex_login", result))
    return origin + urlunsplit(("", "", parts.path, urlencode(query), ""))


def clear_binding(response: Response, state: str | None) -> None:
    if state and len(state) <= 256:
        response.delete_cookie(
            binding_cookie_name(state),
            path=COOKIE_PATH,
            secure=settings.AUTH_COOKIE_SECURE,
            httponly=True,
            samesite="lax",
        )
