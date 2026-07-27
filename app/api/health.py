import asyncio

from fastapi import APIRouter
from redis.asyncio import Redis
from sqlalchemy import text
from starlette.responses import JSONResponse
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.database import engine
from app.redis.settings import settings as redis_settings

health = APIRouter(tags=["health"])


async def _check_database() -> None:
    async with AsyncSession(engine) as session:
        await session.execute(text("SELECT 1"))


async def _check_redis() -> None:
    client = Redis.from_url(redis_settings.REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    finally:
        await client.aclose()


@health.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@health.get("/health/ready")
async def ready():
    results = await asyncio.gather(
        asyncio.wait_for(_check_database(), timeout=2.0),
        asyncio.wait_for(_check_redis(), timeout=2.0),
        return_exceptions=True,
    )
    checks = {
        "database": "ok" if not isinstance(results[0], BaseException) else "unavailable",
        "redis": "ok" if not isinstance(results[1], BaseException) else "unavailable",
    }
    status_code = 200 if all(value == "ok" for value in checks.values()) else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if status_code == 200 else "not_ready",
            "checks": checks,
            "providers": "not_required_for_readiness",
        },
    )
