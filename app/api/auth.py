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

class InitData(BaseModel):
    initData: str

auth = APIRouter(prefix="/auth", tags=["auth"])

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

    result = await session.exec(select(models.AppUser).where(models.AppUser.telegram_id == user_id))
    user = result.first()
    if not user:
        user = models.AppUser(telegram_id=user_id)
        session.add(user)
        await session.commit()
        await session.refresh(user)

    # Check if a user has or had an active subscription
    sub_history = (await session.exec(
        select(UserSubscription).where(UserSubscription.user_id == user.id)
    )).first()

    if not sub_history:
        # NEW USER -> Grant "Starter" Pack
        starter_tier = (await session.exec(
            select(SubscriptionTier).where(SubscriptionTier.name == "Starter")
        )).first()

        if starter_tier:
            new_sub = UserSubscription(
                user_id=user.id,
                tier_id=starter_tier.id,
                status=SubscriptionStatus.active,
                started_at=datetime.now(timezone.utc).replace(tzinfo=None),
                expires_at=None  # No expiration date! It ends when credits run out.
            )
            session.add(new_sub)
            await session.commit()

    access_token = create_access_token(data={"sub": str(user.id)})
    return {"access_token": access_token, "token_type": "bearer"}

@auth.post("/debug-login", response_model=Token)
async def login_debug(form: OAuth2PasswordRequestForm = Depends(),
                      telegram_id: Optional[int] = None, session: AsyncSession = Depends(get_session)):
    if not settings.DEBUG_MODE:
        raise HTTPException(status_code=404, detail="Not Found")

    telegram_id = telegram_id or int(form.username)

    result = await session.exec(select(models.AppUser).where(models.AppUser.telegram_id == telegram_id))
    user = result.first()
    if not user:
        user = models.AppUser(telegram_id=telegram_id)
        session.add(user)
        await session.commit()
        await session.refresh(user)

    access_token = create_access_token(data={"sub": str(user.id)})
    return {"access_token": access_token, "token_type": "bearer"}