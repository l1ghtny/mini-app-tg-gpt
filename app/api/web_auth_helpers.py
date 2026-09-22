import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit, urlunsplit

from fastapi import HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.auth_helpers import ensure_starter_bundle
from app.core.config import settings
from app.api.browser_origins import resolve_browser_origin
from app.core.security import create_access_token
from app.db import models
from app.services.email_service import send_web_login_link
from app.services.subscription_check.entitlements import get_current_subscription

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(value: str) -> str:
    email = value.strip().casefold()
    if len(email) > 320 or not _EMAIL_RE.fullmatch(email):
        raise HTTPException(status_code=422, detail="invalid_email")
    return email


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _callback_url(token: str, origin: str | None = None) -> str:
    selected = resolve_browser_origin(origin) if origin else None
    base = settings.WEB_AUTH_CALLBACK_URL.strip()
    if selected and selected != (settings.WEBAPP_URL or "").rstrip("/"):
        base = f"{selected}/auth/callback"
    if not base:
        webapp = (settings.WEBAPP_URL or "").rstrip("/")
        base = f"{webapp}/auth/callback" if webapp else ""
    if not base:
        raise RuntimeError("WEB_AUTH_CALLBACK_URL or WEBAPP_URL must be configured")
    parts = urlsplit(base)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            parts.query,
            f"token={urlencode({'token': token}).split('=', 1)[1]}",
        )
    )


async def issue_magic_link(
    session: AsyncSession,
    *,
    email: str,
    target_user: models.AppUser | None,
    debug_delivery: bool | None = None,
    origin: str | None = None,
) -> str | None:
    selected = resolve_browser_origin(origin) if origin else None
    normalized = normalize_email(email)
    token = secrets.token_urlsafe(32)
    challenge = models.WebAuthChallenge(
        token_hash=_token_hash(token),
        browser_origin=selected,
        email=normalized,
        target_user_id=target_user.id if target_user else None,
        expires_at=_utcnow_naive()
        + timedelta(minutes=settings.WEB_AUTH_LINK_TTL_MINUTES),
    )
    session.add(challenge)
    await session.commit()

    if debug_delivery is None:
        debug_delivery = settings.DEBUG_MODE or settings.TEST_ENV
    if debug_delivery:
        return token

    try:
        await send_web_login_link(normalized, _callback_url(token, selected))
    except Exception:
        await session.delete(challenge)
        await session.commit()
        raise
    return None


async def consume_magic_link(
    session: AsyncSession,
    *,
    token: str,
    origin: str | None = None,
) -> tuple[str, bool, models.AppUser]:
    now = _utcnow_naive()
    challenge = (
        await session.exec(
            select(models.WebAuthChallenge)
            .where(
                models.WebAuthChallenge.token_hash == _token_hash(token),
                models.WebAuthChallenge.consumed_at.is_(None),
                models.WebAuthChallenge.expires_at > now,
            )
            .with_for_update()
        )
    ).first()
    if not challenge:
        raise HTTPException(status_code=400, detail="invalid_or_expired_login_link")

    if challenge.browser_origin and resolve_browser_origin(origin) != challenge.browser_origin:
        raise HTTPException(status_code=403, detail="web_login_origin_mismatch")

    identity = (
        await session.exec(
            select(models.UserIdentity).where(
                models.UserIdentity.provider == "email",
                models.UserIdentity.subject == challenge.email,
            )
        )
    ).first()

    if challenge.target_user_id:
        if identity and identity.user_id != challenge.target_user_id:
            challenge.consumed_at = now
            session.add(challenge)
            await session.commit()
            raise HTTPException(status_code=409, detail="account_merge_required")
        user = await session.get(models.AppUser, challenge.target_user_id)
        if not user:
            raise HTTPException(status_code=400, detail="login_target_not_found")
    elif identity:
        user = await session.get(models.AppUser, identity.user_id)
        if not user:
            raise HTTPException(status_code=400, detail="login_user_not_found")
    else:
        user = models.AppUser(telegram_id=None)
        session.add(user)
        await session.flush()

    if not identity:
        identity = models.UserIdentity(
            user_id=user.id,
            provider="email",
            subject=challenge.email,
            email=challenge.email,
        )
        session.add(identity)
    else:
        identity.last_used_at = now
        session.add(identity)

    challenge.consumed_at = now
    session.add(challenge)
    await session.commit()
    await session.refresh(user)

    bonus_granted = await ensure_starter_bundle(session, user)
    return create_access_token(data={"sub": str(user.id)}), bonus_granted, user


async def build_user_profile(session: AsyncSession, user: models.AppUser) -> dict:
    identities = (
        await session.exec(
            select(models.UserIdentity).where(models.UserIdentity.user_id == user.id)
        )
    ).all()
    email = next(
        (identity.email for identity in identities if identity.provider == "email"),
        None,
    )
    providers = sorted({identity.provider for identity in identities})
    active_subscription = await get_current_subscription(session, user.id)
    tier_name = active_subscription.tier.name if active_subscription else "free"
    fallback_name = email.split("@", 1)[0] if email else "User"
    return {
        "id": str(user.id),
        "telegram_id": user.telegram_id,
        "first_name": user.telegram_first_name or fallback_name,
        "last_name": user.telegram_last_name,
        "username": user.telegram_username,
        "email": email,
        "language_code": user.preferred_language,
        "photo_url": user.telegram_photo_url,
        "subscription_tier": tier_name,
        "auth_providers": providers,
    }
