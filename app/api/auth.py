from datetime import timezone, datetime
from typing import Optional

from dateutil.relativedelta import relativedelta
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.database import get_session
from app.db import models
from app.core.security import create_access_token, validate_telegram_data
from app.core.config import settings
from pydantic import BaseModel

from app.db.subscription_tiers import UserSubscription, SubscriptionStatus, SubscriptionTier


class Token(BaseModel):
    access_token: str
    token_type: str
    bonus_granted: bool = False

class InitData(BaseModel):
    initData: str

auth = APIRouter(prefix="/auth", tags=["auth"])

logger = settings.custom_logger

@auth.post("/telegram", response_model=Token)
async def login_telegram(data: InitData, session: AsyncSession = Depends(get_session)):
    if settings.TEST_ENV:
        try:
            user_data = validate_telegram_data(data.initData, True)
            user_id = user_data['id']
        except ValueError as e:
            raise HTTPException(status_code=403, detail=str(e))
    else:
        try:
            user_data = validate_telegram_data(data.initData)
            user_id = user_data['id']
        except ValueError as e:
            raise HTTPException(status_code=403, detail=str(e))

    access_token, bonus_granted = await process_login(session, user_id)

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "bonus_granted": bonus_granted
    }

@auth.post("/debug-login", response_model=Token)
async def login_debug(form: OAuth2PasswordRequestForm = Depends(),
                      telegram_id: Optional[int] = None, session: AsyncSession = Depends(get_session)):
    if not settings.DEBUG_MODE:
        raise HTTPException(status_code=404, detail="Not Found")

    telegram_id = telegram_id or int(form.username)#

    access_token, bonus_granted = await process_login(session, telegram_id)


    return {
        "access_token": access_token,
        "token_type": "bearer",
        "bonus_granted": bonus_granted
    }


async def process_login(session: AsyncSession, telegram_id: int):
    result = await session.exec(select(models.AppUser).where(models.AppUser.telegram_id == telegram_id))
    user = result.first()
    if not user:
        user = models.AppUser(telegram_id=telegram_id)
        session.add(user)
        await session.commit()
        await session.refresh(user)

    active_sub = (await session.exec(
        select(UserSubscription).where(
            UserSubscription.user_id == user.id,
            UserSubscription.status == SubscriptionStatus.active
        )
    )).first()

    bonus_granted = False

    # 2. If NO active sub, check if they are eligible for the settings.STARTER_BUNDLE_NAME bonus
    if not active_sub:
        # Check if they have EVER had a settings.STARTER_BUNDLE_NAME subscription
        starter_history = (await session.exec(
            select(UserSubscription)
            .join(SubscriptionTier)
            .where(
                UserSubscription.user_id == user.id,
                SubscriptionTier.name == settings.STARTER_BUNDLE_NAME
            )
        )).first()

        if not starter_history:
            logger.info(f'user {user.id} has no starter history')
            starter_tier = (await session.exec(
                select(SubscriptionTier).where(SubscriptionTier.name == settings.STARTER_BUNDLE_NAME)
            )).first()

            if starter_tier:
                new_sub = UserSubscription(
                    user_id=user.id,
                    tier_id=starter_tier.id,
                    status=SubscriptionStatus.active,
                    started_at=datetime.now(timezone.utc).replace(tzinfo=None),
                    expires_at=None  # Lifetime
                )
                session.add(new_sub)
                await session.commit()
                bonus_granted = True
                logger.info(f'user {user.id} has been granter starter tier')

        else:
            logger.info(f'user {user.id} already has starter tier')
    else:
        logger.info(f'user {user.id} has an active subscription')

    access_token = create_access_token(data={"sub": str(user.id)})

    return access_token, bonus_granted