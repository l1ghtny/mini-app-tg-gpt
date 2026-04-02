import os, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(f"{ROOT}/.env.test", override=True)
except Exception:
    pass

TEST_DB_URL = os.getenv("TEST_DATABASE_URL")
if TEST_DB_URL:
    os.environ["DATABASE_URL"] = TEST_DB_URL
    os.environ.setdefault("TEST_ENV", "1")

import asyncpg
import pytest
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db import models as _models  # noqa: F401
from app.db import subscription_tiers as _subscription_tiers  # noqa: F401


async def _reset_schema(test_db_url: str) -> None:
    url = make_url(test_db_url)
    conn = await asyncpg.connect(
        user=url.username,
        password=url.password,
        database=url.database,
        host=url.host,
        port=url.port,
    )
    try:
        await conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        await conn.execute("CREATE SCHEMA public")
    finally:
        await conn.close()


async def _seed_reference_data(engine) -> None:
    from app.db.models import ImageQualityPricing
    from app.db.subscription_tiers import SubscriptionTier, TierModelLimit

    async with AsyncSession(engine, expire_on_commit=False) as session:
        free = SubscriptionTier(
            name="free",
            name_ru="free",
            description="Free tier",
            description_ru="Free tier",
            price_cents=0,
            monthly_images=10,
            daily_image_limit=2,
            monthly_docs=0,
            monthly_deepsearch=0,
            is_active=True,
            is_public=True,
            index=0,
            is_recurring=False,
        )
        pro = SubscriptionTier(
            name="pro",
            name_ru="pro",
            description="Pro tier",
            description_ru="Pro tier",
            price_cents=1000,
            monthly_images=200,
            daily_image_limit=20,
            monthly_docs=100,
            monthly_deepsearch=100,
            is_active=True,
            is_public=True,
            index=10,
            is_recurring=True,
        )
        session.add(free)
        session.add(pro)
        await session.flush()

        session.add(TierModelLimit(tier_id=free.id, model_name="gpt-5-nano", monthly_requests=100))
        session.add(TierModelLimit(tier_id=pro.id, model_name="gpt-5-nano", monthly_requests=10000))

        session.add_all([
            ImageQualityPricing(image_model="gpt-image-1.5", quality="low", credit_cost=1.0),
            ImageQualityPricing(image_model="gpt-image-1.5", quality="medium", credit_cost=2.0),
            ImageQualityPricing(image_model="gpt-image-1.5", quality="high", credit_cost=3.0),
            ImageQualityPricing(image_model="gpt-image-1.5", quality="standard", credit_cost=1.0),
            ImageQualityPricing(image_model="gpt-image-1.5", quality="auto", credit_cost=1.0),
        ])
        await session.commit()


@pytest.fixture(autouse=True)
async def rebuild_test_db():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    if not test_db_url:
        return

    url = make_url(test_db_url)
    db_name = (url.database or "").lower()
    allow_reset = os.getenv("TEST_DB_ALLOW_RESET") == "1" or "test" in db_name
    if not allow_reset:
        raise RuntimeError(
            "Refusing to reset schema. Set TEST_DB_ALLOW_RESET=1 or use a test database."
        )

    await _reset_schema(test_db_url)

    engine = create_async_engine(test_db_url, future=True, echo=False)
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    await _seed_reference_data(engine)
    await engine.dispose()
