from typing import Any

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from redis.asyncio import Redis
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.db.models import AppUser
from app.redis.settings import settings as redis_settings
from app.db.database import engine, get_session
from app.db import models
from app.redis.event_bus import RedisEventBus
from app.services.subscription_check.entitlements import (
    get_active_subscriptions,
    get_active_usage_packs,
    select_text_entitlement,
)

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


async def rate_limit_check(
        request: Request,
        user: AppUser = Depends(get_current_user),
        redis: Redis =Depends(get_redis)
):
    """
    Spam Protection: Limits users to 60 requests per rolling hour.
    """
    # 1. Config
    LIMIT = 60
    WINDOW = 3600  # 1 hour

    # 2. Key: rate_limit:user_id
    key = f"rl:text:{user.id}"

    # 3. Check usage
    # We use a simple counter with expiry for the fixed window
    # Or a sorted set for rolling window (more expensive).
    # For spam protection, fixed window is fine and faster.

    current = await redis.incr(key)
    if current == 1:
        await redis.expire(key, WINDOW)

    if current > LIMIT:
        raise HTTPException(
            status_code=429,
            detail="You are sending messages too fast. Please take a breather."
        )

    return True


async def get_available_models(current_user: AppUser, session: AsyncSession) -> list:
    subscriptions = await get_active_subscriptions(session, current_user.id)
    packs = await get_active_usage_packs(session, current_user.id)
    model_names: set[str] = set()
    for sub in subscriptions:
        for limit in sub.tier.tier_model_limits:
            model_names.add(limit.model_name)
    for pack in packs:
        for limit in pack.pack.pack_model_limits:
            model_names.add(limit.model_name)

    available_models = []
    for model_name in sorted(model_names):
        ent = await select_text_entitlement(session, current_user.id, model_name)
        if ent["remaining"] > 0:
            available_models.append(model_name)

    return available_models
