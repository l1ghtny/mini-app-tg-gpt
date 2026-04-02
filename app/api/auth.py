from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.database import get_session
from app.core.security import validate_telegram_data
from app.core.config import settings
from pydantic import BaseModel

from app.api.auth_helpers import process_login


class Token(BaseModel):
    access_token: str
    token_type: str
    bonus_granted: bool = False

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

    telegram_id = telegram_id or int(form.username)

    access_token, bonus_granted = await process_login(session, telegram_id)


    return {
        "access_token": access_token,
        "token_type": "bearer",
        "bonus_granted": bonus_granted
    }


