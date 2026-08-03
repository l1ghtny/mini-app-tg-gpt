from datetime import datetime, timezone

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.deployment_channel import ensure_deployment_user_allowed
from app.core.security import create_access_token
from app.db import models
from app.db.subscription_tiers import (
    SubscriptionStatus,
    SubscriptionTier,
    UserSubscription,
)
from app.services.subscription_check.entitlements import get_current_subscription

logger = settings.custom_logger


def _supported_language(language_code: object) -> str | None:
    if not isinstance(language_code, str) or not language_code.strip():
        return None
    primary = language_code.strip().lower().split("-", 1)[0].split("_", 1)[0]
    return "ru" if primary == "ru" else "en"


async def process_login(
    session: AsyncSession,
    telegram_id: int,
    *,
    telegram_profile: dict | None = None,
) -> tuple[str, bool]:
    user = (
        await session.exec(
            select(models.AppUser).where(models.AppUser.telegram_id == telegram_id)
        )
    ).first()
    identity = (
        await session.exec(
            select(models.UserIdentity).where(
                models.UserIdentity.provider == "telegram",
                models.UserIdentity.subject == str(telegram_id),
            )
        )
    ).first()
    if not user and identity:
        user = await session.get(models.AppUser, identity.user_id)
    ensure_deployment_user_allowed(user)
    username = telegram_profile.get("username") if telegram_profile else None
    first_name = telegram_profile.get("first_name") if telegram_profile else None
    last_name = telegram_profile.get("last_name") if telegram_profile else None
    photo_url = telegram_profile.get("photo_url") if telegram_profile else None
    language = _supported_language(
        telegram_profile.get("language_code") if telegram_profile else None
    )

    if not user:
        user = models.AppUser(
            telegram_id=telegram_id,
            telegram_username=username,
            telegram_first_name=first_name,
            telegram_last_name=last_name,
            telegram_photo_url=photo_url,
            preferred_language=language,
        )
        session.add(user)
        await session.flush()
        identity = models.UserIdentity(
            user_id=user.id,
            provider="telegram",
            subject=str(telegram_id),
        )
        session.add(identity)
        await session.commit()
        await session.refresh(user)
    else:
        changed = False
        if user.telegram_id != telegram_id:
            user.telegram_id = telegram_id
            changed = True
        if username is not None and username != user.telegram_username:
            user.telegram_username = username
            changed = True
        if first_name is not None and first_name != user.telegram_first_name:
            user.telegram_first_name = first_name
            changed = True
        if last_name is not None and last_name != user.telegram_last_name:
            user.telegram_last_name = last_name
            changed = True
        if photo_url is not None and photo_url != user.telegram_photo_url:
            user.telegram_photo_url = photo_url
            changed = True
        if user.preferred_language is None and language is not None:
            user.preferred_language = language
            changed = True
        if not identity:
            identity = models.UserIdentity(
                user_id=user.id,
                provider="telegram",
                subject=str(telegram_id),
            )
            session.add(identity)
            changed = True
        else:
            identity.last_used_at = datetime.now(timezone.utc).replace(tzinfo=None)
            session.add(identity)
            changed = True
        if changed:
            session.add(user)
            await session.commit()

    bonus_granted = await ensure_starter_bundle(session, user)
    access_token = create_access_token(data={"sub": str(user.id)})
    return access_token, bonus_granted


async def ensure_starter_bundle(session: AsyncSession, user: models.AppUser) -> bool:
    active_sub = await get_current_subscription(session, user.id)
    bonus_granted = False

    if not active_sub:
        starter_history = (
            await session.exec(
                select(UserSubscription)
                .join(SubscriptionTier)
                .where(
                    UserSubscription.user_id == user.id,
                    SubscriptionTier.name == settings.STARTER_BUNDLE_NAME,
                )
            )
        ).first()

        if not starter_history:
            logger.info("user %s has no starter history", user.id)
            starter_tier = (
                await session.exec(
                    select(SubscriptionTier).where(
                        SubscriptionTier.name == settings.STARTER_BUNDLE_NAME
                    )
                )
            ).first()

            if starter_tier:
                new_sub = UserSubscription(
                    user_id=user.id,
                    tier_id=starter_tier.id,
                    status=SubscriptionStatus.active,
                    started_at=datetime.now(timezone.utc).replace(tzinfo=None),
                    expires_at=None,
                )
                session.add(new_sub)
                await session.commit()
                bonus_granted = True
                logger.info("user %s has been granted starter tier", user.id)
        else:
            logger.info("user %s already has starter tier", user.id)
    else:
        logger.info("user %s has an active subscription", user.id)

    return bonus_granted
