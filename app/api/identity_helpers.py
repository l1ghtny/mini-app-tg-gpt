import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db import models


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def issue_telegram_link(
    session: AsyncSession,
    user: models.AppUser,
) -> tuple[models.TelegramLinkChallenge, str]:
    existing_identity = (
        await session.exec(
            select(models.UserIdentity).where(
                models.UserIdentity.user_id == user.id,
                models.UserIdentity.provider == "telegram",
            )
        )
    ).first()
    if user.telegram_id is not None or existing_identity:
        raise HTTPException(status_code=409, detail="telegram_already_linked")
    token = secrets.token_urlsafe(24)
    challenge = models.TelegramLinkChallenge(
        target_user_id=user.id,
        token_hash=_token_hash(token),
        expires_at=_utcnow_naive() + timedelta(minutes=15),
    )
    session.add(challenge)
    await session.commit()
    await session.refresh(challenge)
    return challenge, token


async def consume_telegram_link(
    session: AsyncSession,
    *,
    token: str,
    telegram_id: int,
    first_name: str | None,
    last_name: str | None,
    username: str | None,
) -> models.TelegramLinkChallenge | None:
    now = _utcnow_naive()
    challenge = (
        await session.exec(
            select(models.TelegramLinkChallenge)
            .where(
                models.TelegramLinkChallenge.token_hash == _token_hash(token),
                models.TelegramLinkChallenge.status == "pending",
            )
            .with_for_update()
        )
    ).first()
    if not challenge:
        return None
    if challenge.expires_at <= now:
        challenge.status = "expired"
        challenge.consumed_at = now
        session.add(challenge)
        await session.commit()
        return challenge

    target = await session.get(models.AppUser, challenge.target_user_id)
    telegram_user = (
        await session.exec(
            select(models.AppUser).where(models.AppUser.telegram_id == telegram_id)
        )
    ).first()
    telegram_identity = (
        await session.exec(
            select(models.UserIdentity).where(
                models.UserIdentity.provider == "telegram",
                models.UserIdentity.subject == str(telegram_id),
            )
        )
    ).first()
    if not target or target.deleted_at is not None:
        challenge.status = "expired"
    elif telegram_user and telegram_user.id != target.id:
        challenge.status = "conflict"
        challenge.conflicting_user_id = telegram_user.id
    elif telegram_identity and telegram_identity.user_id != target.id:
        challenge.status = "conflict"
        challenge.conflicting_user_id = telegram_identity.user_id
    elif target.telegram_id not in (None, telegram_id):
        challenge.status = "conflict"
    else:
        target.telegram_id = telegram_id
        target.telegram_first_name = first_name
        target.telegram_last_name = last_name
        target.telegram_username = username
        session.add(target)
        if not telegram_identity:
            session.add(
                models.UserIdentity(
                    user_id=target.id,
                    provider="telegram",
                    subject=str(telegram_id),
                )
            )
        challenge.status = "linked"

    challenge.telegram_id = telegram_id
    challenge.consumed_at = now
    session.add(challenge)
    await session.commit()
    return challenge
