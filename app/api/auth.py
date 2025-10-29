from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.database import get_session
from app.db import models
from app.core.security import create_access_token, validate_telegram_data
from app.core.config import settings
from pydantic import BaseModel

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

    access_token = create_access_token(data={"sub": str(user.id)})
    return {"access_token": access_token, "token_type": "bearer"}

@auth.post("/debug-login", response_model=Token)
async def login_debug(form: OAuth2PasswordRequestForm = Depends(),
                      telegram_id: Optional[int] = None, session: AsyncSession = Depends(get_session)):
    if not settings.DEBUG_MODE:
        raise HTTPException(status_code=404, detail="Not Found")
    print('lol')

    telegram_id = telegram_id or int(form.username)
    print(form)
    print(telegram_id)

    result = await session.exec(select(models.AppUser).where(models.AppUser.telegram_id == telegram_id))
    user = result.first()
    if not user:
        user = models.AppUser(telegram_id=telegram_id)
        session.add(user)
        await session.commit()
        await session.refresh(user)

    access_token = create_access_token(data={"sub": str(user.id)})
    return {"access_token": access_token, "token_type": "bearer"}