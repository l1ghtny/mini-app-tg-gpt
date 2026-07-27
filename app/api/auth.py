import hashlib
import ipaddress
import smtplib
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
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
from app.api.identity_helpers import issue_telegram_link
from app.api.web_auth_helpers import (
    build_user_profile,
    consume_magic_link,
    issue_magic_link,
    normalize_email,
)
from app.core.config import settings
from app.core.security import validate_telegram_data
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


auth = APIRouter(prefix="/auth", tags=["auth"])


async def _enforce_magic_link_rate_limits(redis: Redis, request: Request, email: str) -> None:
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
        settings.custom_logger.error("Invalid WEB_AUTH_TRUSTED_PROXY_CIDRS configuration")
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

    access_token, bonus_granted = await process_login(
        session,
        user_id,
        telegram_profile=user_data,
    )
    user = (await session.exec(
        select(AppUser).where(AppUser.telegram_id == user_id)
    )).first()
    set_session_cookie(response, await create_browser_session(session, user, request))
    return Token(
        access_token=access_token,
        token_type="bearer",
        bonus_granted=bonus_granted,
        user=UserProfile(**await build_user_profile(session, user)),
    )


@auth.post("/debug-login", response_model=Token)
async def login_debug(
    request: Request,
    response: Response,
    form: OAuth2PasswordRequestForm = Depends(),
    telegram_id: int | None = None,
    session: AsyncSession = Depends(get_session),
):
    if not settings.DEBUG_MODE:
        raise HTTPException(status_code=404, detail="Not Found")

    resolved_telegram_id = telegram_id or int(form.username)
    access_token, bonus_granted = await process_login(session, resolved_telegram_id)
    user = (await session.exec(
        select(AppUser).where(AppUser.telegram_id == resolved_telegram_id)
    )).first()
    set_session_cookie(response, await create_browser_session(session, user, request))
    return Token(
        access_token=access_token,
        token_type="bearer",
        bonus_granted=bonus_granted,
        user=UserProfile(**await build_user_profile(session, user)),
    )


@auth.post("/web/email/request", response_model=MagicLinkAccepted, status_code=status.HTTP_202_ACCEPTED)
async def request_email_magic_link(
    payload: MagicLinkRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
    current_user: AppUser | None = Depends(get_optional_current_user),
):
    if not settings.WEB_AUTH_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")

    email = normalize_email(payload.email)
    await _enforce_magic_link_rate_limits(redis, request, email)
    try:
        debug_token = await issue_magic_link(session, email=email, target_user=current_user)
    except (RuntimeError, OSError, smtplib.SMTPException) as exc:
        settings.custom_logger.exception("Web login email delivery is unavailable")
        raise HTTPException(status_code=503, detail="login_email_unavailable") from exc
    return MagicLinkAccepted(debug_token=debug_token)


@auth.post("/web/email/verify", response_model=Token)
async def verify_email_magic_link(
    payload: MagicLinkVerifyRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    if not settings.WEB_AUTH_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")

    access_token, bonus_granted, user = await consume_magic_link(session, token=payload.token)
    set_session_cookie(response, await create_browser_session(session, user, request))
    return Token(
        access_token=access_token,
        token_type="bearer",
        bonus_granted=bonus_granted,
        user=UserProfile(**await build_user_profile(session, user)),
    )


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
    current_hash = hashlib.sha256(current_token.encode()).hexdigest() if current_token else None
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
    current_hash = hashlib.sha256(current_token.encode()).hexdigest() if current_token else None
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
        recovery_reference=str(challenge.id) if challenge.status == "conflict" else None,
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
            select(models.UserIdentity).where(models.UserIdentity.user_id == current_user.id)
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
