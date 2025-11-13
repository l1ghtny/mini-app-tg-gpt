from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from redis.asyncio import Redis
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.db.models import AppUser
from app.redis.settings import settings as redis_settings
from app.db.database import engine
from app.db import models
from app.redis.event_bus import RedisEventBus

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/debug-login", scheme_name="Bearer")

async def get_current_user(
    token: str = Depends(oauth2_scheme),
) -> AppUser | None:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    async with AsyncSession(engine) as session:
        result = await session.exec(select(models.AppUser).where(models.AppUser.id == user_id))
        user = result.first()
        if user is None:
            raise credentials_exception
    return user


async def get_redis() -> Redis:
    return Redis.from_url(redis_settings.REDIS_URL, decode_responses=True)


async def get_bus(redis: Redis = Depends(get_redis)) -> RedisEventBus:
    return RedisEventBus(redis)