import hashlib
import ipaddress
import smtplib
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.auth_helpers import process_login
from app.api.dependencies import get_current_user, get_optional_current_user, get_redis
from app.api.session_helpers import (
    clear_session_cookie,
    create_browser_session,
    revoke_browser_session,
    set_session_cookie,
)
from app.api.telegram_oidc import (
    begin_telegram_oidc,
    complete_telegram_oidc,
    frontend_redirect,
)
from app.api.identity_helpers import issue_telegram_link
from app.api.passkey_helpers import (
    begin_passkey_authentication,
    begin_passkey_registration,
    finish_passkey_authentication,
    finish_passkey_registration,
    resolve_passkey_context,
)
from app.api.web_auth_helpers import (
    build_user_profile,
    consume_magic_link,
    issue_magic_link,
    normalize_email,
)
from app.core.config import settings
from app.core.deployment_channel import (
    ensure_deployment_user_allowed,
    is_beta_channel,
)
from app.core.security import create_access_token, validate_telegram_data
from app.db import models
from app.db.database import get_session
from app.db.models import AppUser


class UserProfile(BaseModel):
    id: str
    telegram_id: int | None = None
    first_name: str
    last_name: str | None = None
    username: str | None = None
    email: str | None = None
    language_code: str | None = None
    photo_url: str | None = None
    subscription_tier: str = "free"
    auth_providers: list[str] = Field(default_factory=list)


class Token(BaseModel):
    access_token: str
    token_type: str
    bonus_granted: bool = False
    user: UserProfile | None = None


class InitData(BaseModel):
    initData: str


class MagicLinkRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class MagicLinkVerifyRequest(BaseModel):
    token: str = Field(min_length=20, max_length=512)


class MagicLinkAccepted(BaseModel):
    accepted: bool = True
    debug_token: str | None = None


class PasskeyOptionsResponse(BaseModel):
    ceremony_id: str
    options: dict[str, Any]


class PasskeyRegistrationFinish(BaseModel):
    ceremony_id: str = Field(min_length=20, max_length=256)
    credential: dict[str, Any]
    name: str | None = Field(default=None, max_length=80)


class PasskeyAuthenticationFinish(BaseModel):
    ceremony_id: str = Field(min_length=20, max_length=256)
    credential: dict[str, Any]


class PasskeyView(BaseModel):
    id: str
    name: str
    device_type: str | None
    backed_up: bool
    transports: list[str]
    created_at: str
    last_used_at: str | None


class BrowserSessionView(BaseModel):
    id: str
    current: bool
    user_agent: str | None
    created_at: str
    last_seen_at: str
    expires_at: str


class TelegramLinkView(BaseModel):
    id: str
    status: str
    start_parameter: str | None = None
    recovery_reference: str | None = None


def _passkey_view(passkey: models.PasskeyCredential) -> PasskeyView:
    return PasskeyView(
        id=str(passkey.id),
        name=passkey.name,
        device_type=passkey.device_type,
        backed_up=passkey.backed_up,
        transports=passkey.transports,
        created_at=passkey.created_at.isoformat(),
        last_used_at=passkey.last_used_at.isoformat() if passkey.last_used_at else None,
    )


auth = APIRouter(prefix="/auth", tags=["auth"])


async def _enforce_magic_link_rate_limits(
    redis: Redis, request: Request, email: str
) -> None:
    ip = _resolve_client_ip(request)
    keys = [
        (f"rl:web-auth:email:{hashlib.sha256(email.encode()).hexdigest()}", 5),
        ("rl:web-auth:global", 1000),
    ]
    if ip:
        keys.append((f"rl:web-auth:ip:{hashlib.sha256(ip.encode()).hexdigest()}", 20))
    for key, limit in keys:
        current = await redis.incr(key)
        if current == 1:
            await redis.expire(key, 3600)
        if current > limit:
            raise HTTPException(status_code=429, detail="too_many_login_attempts")


def _resolve_client_ip(request: Request) -> str | None:
    if not request.client:
        return None
    try:
        peer = ipaddress.ip_address(request.client.host)
    except ValueError:
        return None

    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return str(peer)

    try:
        trusted = tuple(
            ipaddress.ip_network(cidr, strict=False)
            for cidr in settings.WEB_AUTH_TRUSTED_PROXY_CIDRS
        )
    except ValueError:
        settings.custom_logger.error(
            "Invalid WEB_AUTH_TRUSTED_PROXY_CIDRS configuration"
        )
        return None
    if not trusted or not any(peer in network for network in trusted):
        return None

    try:
        chain = [ipaddress.ip_address(item.strip()) for item in forwarded.split(",")]
    except ValueError:
        return None
    for address in reversed(chain):
        if not any(address in network for network in trusted):
            return str(address)
    return str(chain[0]) if chain else None


def _allow_local_debug_request(request: Request) -> bool:
    if settings.TEST_ENV:
        return True
    if not settings.DEBUG_MODE or not request.client:
        return False
    try:
        peer_is_loopback = ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        return False
    origin_host = (urlsplit(request.headers.get("origin", "")).hostname or "").lower()
    return peer_is_loopback and origin_host in {"localhost", "127.0.0.1", "::1"}


def _allow_debug_magic_link(request: Request) -> bool:
    return _allow_local_debug_request(request)


async def _enforce_passkey_rate_limits(
    redis: Redis,
    request: Request,
    *,
    account_id: uuid.UUID | None = None,
    challenge_id: str | None = None,
) -> None:
    keys = [("rl:passkey:global", 2000, 3600)]
    if account_id:
        account_hash = hashlib.sha256(str(account_id).encode()).hexdigest()
        keys.append((f"rl:passkey:account:{account_hash}", 30, 3600))
    if challenge_id:
        challenge_hash = hashlib.sha256(challenge_id.encode()).hexdigest()
        keys.append(
            (
                f"rl:passkey:challenge:{challenge_hash}",
                3,
                settings.PASSKEY_CHALLENGE_TTL_SECONDS,
            )
        )
    ip = _resolve_client_ip(request)
    if ip:
        keys.append(
            (f"rl:passkey:ip:{hashlib.sha256(ip.encode()).hexdigest()}", 120, 3600)
        )
    for key, limit, ttl in keys:
        current = await redis.incr(key)
        if current == 1:
            await redis.expire(key, ttl)
        if current > limit:
            raise HTTPException(status_code=429, detail="too_many_passkey_attempts")


async def _passkey_account_id(
    session: AsyncSession, credential: dict[str, Any]
) -> uuid.UUID | None:
    credential_id = credential.get("id") if isinstance(credential, dict) else None
    if not isinstance(credential_id, str) or not credential_id:
        return None
    passkey = (
        await session.exec(
            select(models.PasskeyCredential).where(
                models.PasskeyCredential.credential_id == credential_id
            )
        )
    ).first()
    return passkey.user_id if passkey else None


async def _ensure_existing_beta_telegram_user_allowed(
    session: AsyncSession, telegram_id: int
) -> None:
    if not is_beta_channel():
        return
    existing_user = (
        await session.exec(select(AppUser).where(AppUser.telegram_id == telegram_id))
    ).first()
    ensure_deployment_user_allowed(existing_user)


@auth.post("/telegram", response_model=Token)
async def login_telegram(
    data: InitData,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    try:
        user_data = validate_telegram_data(data.initData, settings.TEST_ENV)
        user_id = user_data["id"]
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    await _ensure_existing_beta_telegram_user_allowed(session, user_id)
    access_token, bonus_granted = await process_login(
        session,
        user_id,
        telegram_profile=user_data,
    )
    user = (
        await session.exec(select(AppUser).where(AppUser.telegram_id == user_id))
    ).first()
    ensure_deployment_user_allowed(user)
    set_session_cookie(response, await create_browser_session(session, user, request))
    return Token(
        access_token=access_token,
        token_type="bearer",
        bonus_granted=bonus_granted,
        user=UserProfile(**await build_user_profile(session, user)),
    )


@auth.get("/telegram/oidc/start")
async def start_telegram_oidc_login(
    return_to: str = "/",
    redis: Redis = Depends(get_redis),
) -> RedirectResponse:
    authorization_url = await begin_telegram_oidc(redis, return_to=return_to)
    return RedirectResponse(authorization_url, status_code=status.HTTP_302_FOUND)


@auth.get("/telegram/oidc/callback")
async def finish_telegram_oidc_login(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> RedirectResponse:
    if error or not code or not state:
        return RedirectResponse(
            frontend_redirect("/", "cancelled" if error else "error"),
            status_code=status.HTTP_302_FOUND,
        )
    try:
        identity = await complete_telegram_oidc(redis, code=code, state=state)
        await _ensure_existing_beta_telegram_user_allowed(session, identity.telegram_id)
        await process_login(
            session,
            identity.telegram_id,
            telegram_profile=identity.profile,
        )
        user = (
            await session.exec(
                select(AppUser).where(AppUser.telegram_id == identity.telegram_id)
            )
        ).first()
        if not user:
            raise HTTPException(status_code=500, detail="telegram_login_user_missing")
        ensure_deployment_user_allowed(user)
    except HTTPException:
        settings.custom_logger.warning("Telegram browser login failed", exc_info=True)
        return RedirectResponse(
            frontend_redirect("/", "error"),
            status_code=status.HTTP_302_FOUND,
        )

    response = RedirectResponse(
        frontend_redirect(identity.return_to, "success"),
        status_code=status.HTTP_302_FOUND,
    )
    set_session_cookie(response, await create_browser_session(session, user, request))
    return response


@auth.post("/debug-login", response_model=Token)
async def login_debug(
    request: Request,
    response: Response,
    form: OAuth2PasswordRequestForm = Depends(),
    telegram_id: int | None = None,
    session: AsyncSession = Depends(get_session),
):
    if not settings.DEBUG_MODE or not _allow_local_debug_request(request):
        raise HTTPException(status_code=404, detail="Not Found")

    resolved_telegram_id = telegram_id or int(form.username)
    access_token, bonus_granted = await process_login(session, resolved_telegram_id)
    user = (
        await session.exec(
            select(AppUser).where(AppUser.telegram_id == resolved_telegram_id)
        )
    ).first()
    set_session_cookie(response, await create_browser_session(session, user, request))
    return Token(
        access_token=access_token,
        token_type="bearer",
        bonus_granted=bonus_granted,
        user=UserProfile(**await build_user_profile(session, user)),
    )


async def _request_email_challenge(
    payload: MagicLinkRequest,
    request: Request,
    session: AsyncSession,
    redis: Redis,
    target_user: AppUser | None,
) -> MagicLinkAccepted:
    if not settings.WEB_AUTH_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")

    email = normalize_email(payload.email)
    await _enforce_magic_link_rate_limits(redis, request, email)
    try:
        debug_token = await issue_magic_link(
            session,
            email=email,
            target_user=target_user,
            debug_delivery=_allow_debug_magic_link(request),
        )
    except (RuntimeError, OSError, smtplib.SMTPException) as exc:
        settings.custom_logger.exception("Web login email delivery is unavailable")
        raise HTTPException(status_code=503, detail="login_email_unavailable") from exc
    return MagicLinkAccepted(debug_token=debug_token)


@auth.post(
    "/web/email/request",
    response_model=MagicLinkAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    deprecated=True,
)
async def request_email_magic_link(
    payload: MagicLinkRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
    current_user: AppUser | None = Depends(get_optional_current_user),
):
    """Legacy endpoint preserving the former session-inferred behavior."""
    return await _request_email_challenge(
        payload, request, session, redis, current_user
    )


@auth.post(
    "/web/email/login/request",
    response_model=MagicLinkAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_email_login_link(
    payload: MagicLinkRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
):
    """Issue an account-neutral email login challenge.

    Authentication credentials on the request are intentionally ignored. A
    verified email identity always resolves to its existing owner when the
    challenge is consumed.
    """
    return await _request_email_challenge(payload, request, session, redis, None)


@auth.post(
    "/web/email/link/request",
    response_model=MagicLinkAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_email_identity_link(
    payload: MagicLinkRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
    current_user: AppUser = Depends(get_current_user),
):
    """Issue an email challenge explicitly bound to the current account."""
    return await _request_email_challenge(
        payload, request, session, redis, current_user
    )


@auth.post("/web/email/verify", response_model=Token)
async def verify_email_magic_link(
    payload: MagicLinkVerifyRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    if not settings.WEB_AUTH_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")

    access_token, bonus_granted, user = await consume_magic_link(
        session, token=payload.token
    )
    ensure_deployment_user_allowed(user)
    set_session_cookie(response, await create_browser_session(session, user, request))
    return Token(
        access_token=access_token,
        token_type="bearer",
        bonus_granted=bonus_granted,
        user=UserProfile(**await build_user_profile(session, user)),
    )


@auth.post("/passkeys/registration/options", response_model=PasskeyOptionsResponse)
async def passkey_registration_options(
    request: Request,
    current_user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
):
    if not settings.WEB_AUTH_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")
    await _enforce_passkey_rate_limits(redis, request, account_id=current_user.id)
    origin, rp_id = resolve_passkey_context(request)
    ceremony_id, options = await begin_passkey_registration(
        session,
        redis,
        user=current_user,
        origin=origin,
        rp_id=rp_id,
    )
    return PasskeyOptionsResponse(ceremony_id=ceremony_id, options=options)


@auth.post("/passkeys/registration/verify", response_model=PasskeyView)
async def passkey_registration_verify(
    payload: PasskeyRegistrationFinish,
    request: Request,
    current_user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
):
    if not settings.WEB_AUTH_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")
    await _enforce_passkey_rate_limits(
        redis,
        request,
        account_id=current_user.id,
        challenge_id=payload.ceremony_id,
    )
    passkey = await finish_passkey_registration(
        session,
        redis,
        user=current_user,
        ceremony_id=payload.ceremony_id,
        credential=payload.credential,
        name=payload.name,
    )
    return _passkey_view(passkey)


@auth.post("/passkeys/authentication/options", response_model=PasskeyOptionsResponse)
async def passkey_authentication_options(
    request: Request,
    redis: Redis = Depends(get_redis),
):
    if not settings.WEB_AUTH_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")
    await _enforce_passkey_rate_limits(redis, request)
    origin, rp_id = resolve_passkey_context(request)
    ceremony_id, options = await begin_passkey_authentication(
        redis,
        origin=origin,
        rp_id=rp_id,
    )
    return PasskeyOptionsResponse(ceremony_id=ceremony_id, options=options)


@auth.post("/passkeys/authentication/verify", response_model=Token)
async def passkey_authentication_verify(
    payload: PasskeyAuthenticationFinish,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
):
    if not settings.WEB_AUTH_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")
    account_id = await _passkey_account_id(session, payload.credential)
    await _enforce_passkey_rate_limits(
        redis,
        request,
        account_id=account_id,
        challenge_id=payload.ceremony_id,
    )
    user, _ = await finish_passkey_authentication(
        session,
        redis,
        ceremony_id=payload.ceremony_id,
        credential=payload.credential,
    )
    ensure_deployment_user_allowed(user)
    access_token = create_access_token(data={"sub": str(user.id)})
    set_session_cookie(response, await create_browser_session(session, user, request))
    return Token(
        access_token=access_token,
        token_type="bearer",
        user=UserProfile(**await build_user_profile(session, user)),
    )


@auth.get("/passkeys", response_model=list[PasskeyView])
async def list_passkeys(
    current_user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    passkeys = (
        await session.exec(
            select(models.PasskeyCredential)
            .where(models.PasskeyCredential.user_id == current_user.id)
            .order_by(models.PasskeyCredential.created_at.desc())
        )
    ).all()
    return [_passkey_view(passkey) for passkey in passkeys]


@auth.delete("/passkeys/{passkey_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_passkey(
    passkey_id: uuid.UUID,
    current_user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    passkey = await session.get(models.PasskeyCredential, passkey_id)
    if not passkey or passkey.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="passkey_not_found")
    await session.delete(passkey)
    await session.commit()


@auth.get("/me", response_model=UserProfile)
async def get_me(
    current_user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return UserProfile(**await build_user_profile(session, current_user))


@auth.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> None:
    token = request.cookies.get(settings.AUTH_COOKIE_NAME)
    if token:
        await revoke_browser_session(session, token)
    clear_session_cookie(response)


@auth.get("/sessions", response_model=list[BrowserSessionView])
async def list_browser_sessions(
    request: Request,
    current_user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    current_token = request.cookies.get(settings.AUTH_COOKIE_NAME)
    current_hash = (
        hashlib.sha256(current_token.encode()).hexdigest() if current_token else None
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    items = (
        await session.exec(
            select(models.BrowserSession)
            .where(
                models.BrowserSession.user_id == current_user.id,
                models.BrowserSession.revoked_at.is_(None),
                models.BrowserSession.expires_at > now,
            )
            .order_by(models.BrowserSession.last_seen_at.desc())
        )
    ).all()
    return [
        BrowserSessionView(
            id=str(item.id),
            current=item.token_hash == current_hash,
            user_agent=item.user_agent,
            created_at=item.created_at.isoformat(),
            last_seen_at=item.last_seen_at.isoformat(),
            expires_at=item.expires_at.isoformat(),
        )
        for item in items
    ]


@auth.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    session_id: uuid.UUID,
    current_user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    item = await session.get(models.BrowserSession, session_id)
    if not item or item.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="session_not_found")
    item.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(item)
    await session.commit()


@auth.delete("/sessions", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_other_sessions(
    request: Request,
    current_user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    current_token = request.cookies.get(settings.AUTH_COOKIE_NAME)
    current_hash = (
        hashlib.sha256(current_token.encode()).hexdigest() if current_token else None
    )
    items = (
        await session.exec(
            select(models.BrowserSession).where(
                models.BrowserSession.user_id == current_user.id,
                models.BrowserSession.revoked_at.is_(None),
            )
        )
    ).all()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for item in items:
        if item.token_hash != current_hash:
            item.revoked_at = now
            session.add(item)
    await session.commit()


@auth.post("/identities/telegram/link", response_model=TelegramLinkView)
async def start_telegram_link(
    current_user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    challenge, token = await issue_telegram_link(session, current_user)
    return TelegramLinkView(
        id=str(challenge.id),
        status=challenge.status,
        start_parameter=f"link_{token}",
    )


@auth.get("/identities/telegram/link/{challenge_id}", response_model=TelegramLinkView)
async def get_telegram_link(
    challenge_id: uuid.UUID,
    current_user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    challenge = await session.get(models.TelegramLinkChallenge, challenge_id)
    if not challenge or challenge.target_user_id != current_user.id:
        raise HTTPException(status_code=404, detail="link_challenge_not_found")
    return TelegramLinkView(
        id=str(challenge.id),
        status=challenge.status,
        recovery_reference=str(challenge.id)
        if challenge.status == "conflict"
        else None,
    )


@auth.delete("/identities/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_identity(
    provider: str,
    current_user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    if provider not in {"email", "telegram"}:
        raise HTTPException(status_code=404, detail="identity_not_found")
    identities = (
        await session.exec(
            select(models.UserIdentity).where(
                models.UserIdentity.user_id == current_user.id
            )
        )
    ).all()
    identity = next((item for item in identities if item.provider == provider), None)
    if not identity:
        raise HTTPException(status_code=404, detail="identity_not_found")
    if len({item.provider for item in identities}) <= 1:
        raise HTTPException(status_code=409, detail="last_identity_cannot_be_removed")
    await session.delete(identity)
    if provider == "telegram":
        current_user.telegram_id = None
        current_user.telegram_username = None
        current_user.telegram_first_name = None
        current_user.telegram_last_name = None
        session.add(current_user)
    await session.commit()
