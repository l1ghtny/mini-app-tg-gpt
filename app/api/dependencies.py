from typing import Any

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from redis.asyncio import Redis
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.db.models import AppUser
from app.db.subscription_tiers import TierModelLimit
from app.redis.settings import settings as redis_settings
from app.db.database import engine, get_session
from app.db import models
from app.redis.event_bus import RedisEventBus
from app.services.subscription_check.entitlements import remaining_requests_for_model

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
        redis=Depends(RedisEventBus)
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


async def get_available_models(current_user: AppUser, tier: TierModelLimit, session: AsyncSession = get_session()) -> list:
    available_models = []

    # 1. Get all limits for this tier
    limits = await session.exec(
        select(TierModelLimit).where(TierModelLimit.tier_id == tier.id)
    )

    # 2. Check remaining for each
    for limit in limits.all():
        rem = await remaining_requests_for_model(session, current_user.id, tier.id, limit.model_name)
        if rem > 0:
            available_models.append(limit.model_name)

    return available_models