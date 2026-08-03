import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Request, Response
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.deployment_channel import ensure_deployment_user_allowed
from app.db import models


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _session_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_browser_session(
    session: AsyncSession,
    user: models.AppUser,
    request: Request | None = None,
) -> str:
    ensure_deployment_user_allowed(user)
    token = secrets.token_urlsafe(32)
    now = _utcnow_naive()
    user_agent = request.headers.get("user-agent", "")[:512] if request else None
    session.add(
        models.BrowserSession(
            user_id=user.id,
            token_hash=_session_token_hash(token),
            user_agent=user_agent or None,
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(days=settings.BROWSER_SESSION_TTL_DAYS),
        )
    )
    await session.commit()
    return token


async def resolve_browser_session(
    session: AsyncSession,
    token: str,
) -> tuple[models.AppUser, models.BrowserSession] | None:
    now = _utcnow_naive()
    browser_session = (
        await session.exec(
            select(models.BrowserSession).where(
                models.BrowserSession.token_hash == _session_token_hash(token),
                models.BrowserSession.revoked_at.is_(None),
                models.BrowserSession.expires_at > now,
            )
        )
    ).first()
    if not browser_session:
        return None
    user = await session.get(models.AppUser, browser_session.user_id)
    if not user or user.deleted_at is not None:
        return None
    if browser_session.last_seen_at < now - timedelta(minutes=5):
        browser_session.last_seen_at = now
        session.add(browser_session)
        await session.commit()
    return user, browser_session


async def revoke_browser_session(session: AsyncSession, token: str) -> bool:
    browser_session = (
        await session.exec(
            select(models.BrowserSession).where(
                models.BrowserSession.token_hash == _session_token_hash(token),
                models.BrowserSession.revoked_at.is_(None),
            )
        )
    ).first()
    if not browser_session:
        return False
    browser_session.revoked_at = _utcnow_naive()
    session.add(browser_session)
    await session.commit()
    return True


def set_session_cookie(response: Response, token: str) -> None:
    same_site = settings.AUTH_COOKIE_SAMESITE
    if same_site not in {"lax", "strict"}:
        raise RuntimeError("AUTH_COOKIE_SAMESITE must be 'lax' or 'strict'")
    response.set_cookie(
        key=settings.AUTH_COOKIE_NAME,
        value=token,
        max_age=settings.AUTH_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite=same_site,
        domain=settings.AUTH_COOKIE_DOMAIN,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.AUTH_COOKIE_NAME,
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite=settings.AUTH_COOKIE_SAMESITE,
        domain=settings.AUTH_COOKIE_DOMAIN,
        path="/",
    )
