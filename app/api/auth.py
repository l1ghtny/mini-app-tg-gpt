from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
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
def login_telegram(data: InitData, session: Session = Depends(get_session)):
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

    user = session.exec(select(models.AppUser).where(models.AppUser.telegram_id == user_id)).first()
    if not user:
        user = models.AppUser(telegram_id=user_id)
        session.add(user)
        session.commit()
        session.refresh(user)

    access_token = create_access_token(data={"sub": str(user.id)})
    return {"access_token": access_token, "token_type": "bearer"}


@auth.post("/debug-login", response_model=Token)
def login_debug(telegram_id: int, session: Session = Depends(get_session)):
    if not settings.DEBUG_MODE:
        raise HTTPException(status_code=404, detail="Not Found")

    user = session.exec(select(models.AppUser).where(models.AppUser.telegram_id == telegram_id)).first()
    if not user:
        user = models.AppUser(telegram_id=telegram_id)
        session.add(user)
        session.commit()
        session.refresh(user)

    access_token = create_access_token(data={"sub": str(user.id)})
    return {"access_token": access_token, "token_type": "bearer"}


# import hmac
# import hashlib
# import json
# from urllib.parse import unquote
# import time
# from fastapi import APIRouter, HTTPException
# from pydantic import BaseModel
#
# # Add JWT imports and security helpers in a bit
#
# router = APIRouter()
#
# # You'd get this from your .env file
# BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
#
#
# class AuthRequest(BaseModel):
#     initData: str
#
#
# def validate_telegram_data(init_data: str) -> dict:
#     """
#     Validates the initData string from Telegram.
#     """
#     try:
#         # Split and sort the data
#         data_check_string = []
#         received_hash = ''
#         params = sorted([unquote(p) for p in init_data.split('&')])
#
#         for param in params:
#             key, value = param.split('=', 1)
#             if key == 'hash':
#                 received_hash = value
#             else:
#                 data_check_string.append(f"{key}={value}")
#
#         # Create the secret key
#         secret_key = hmac.new("WebAppData".encode(), BOT_TOKEN.encode(), hashlib.sha256).digest()
#         # Create the hash
#         calculated_hash = hmac.new(secret_key, "\n".join(data_check_string).encode(), hashlib.sha256).hexdigest()
#
#         # Compare hashes
#         if calculated_hash != received_hash:
#             raise HTTPException(status_code=403, detail="Invalid hash")
#
#         # Optional: Check if data is outdated
#         user_data = json.loads([p for p in data_check_string if p.startswith('user=')][0].split('=', 1)[1])
#         if time.time() - int(user_data['auth_date']) > 3600: # 1 hour
#             raise HTTPException(status_code=403, detail="Data is outdated")
#
#         return {k: v for k, v in [p.split('=', 1) for p in data_check_string]}
#
#     except Exception:
#         raise HTTPException(status_code=400, detail="Invalid initData")
#
#
# @router.post("/auth/telegram")
# def authenticate_telegram_user(request: AuthRequest):
#     # This is a simplified version. A full implementation would:
#     # 1. Call validate_telegram_data
#     # 2. Extract the user's Telegram ID
#     # 3. Find or create a user in your database
#     # 4. Create and return a JWT session token
#
#     # For now, let's just validate and return a success message
#     validated_data = validate_telegram_data(request.initData)
#     return {"status": "ok", "user_data": validated_data}