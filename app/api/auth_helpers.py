from datetime import datetime, timezone

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.security import create_access_token
from app.db import models
from app.db.subscription_tiers import SubscriptionStatus, SubscriptionTier, UserSubscription
from app.services.subscription_check.entitlements import get_current_subscription

logger = settings.custom_logger


async def process_login(session: AsyncSession, telegram_id: int) -> tuple[str, bool]:
    result = await session.exec(select(models.AppUser).where(models.AppUser.telegram_id == telegram_id))
    user = result.first()
    if not user:
        user = models.AppUser(telegram_id=telegram_id)
        session.add(user)
        await session.commit()
        await session.refresh(user)

    active_sub = await get_current_subscription(session, user.id)
    bonus_granted = False

    if not active_sub:
        starter_history = (await session.exec(
            select(UserSubscription)
            .join(SubscriptionTier)
            .where(
                UserSubscription.user_id == user.id,
                SubscriptionTier.name == settings.STARTER_BUNDLE_NAME,
            )
        )).first()

        if not starter_history:
            logger.info("user %s has no starter history", user.id)
            starter_tier = (await session.exec(
                select(SubscriptionTier).where(SubscriptionTier.name == settings.STARTER_BUNDLE_NAME)
            )).first()

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

    access_token = create_access_token(data={"sub": str(user.id)})
    return access_token, bonus_granted
