import os

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.user_usage_helpers import get_image_usage
from app.db import models as m
from app.db.models import ImageQualityPricing
from app.db.subscription_tiers import (
    SubscriptionStatus,
    SubscriptionTier,
    TierImageModelLimit,
    UserSubscription,
)
from app.services.subscription_check.pacing import get_image_quality_pricing


@pytest.mark.asyncio
async def test_google_image_usage_ignores_legacy_quality_aliases():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = m.AppUser(telegram_id=721000801)
        session.add(user)
        await session.flush()

        tier = (await session.exec(select(SubscriptionTier).where(SubscriptionTier.name == "free"))).one()
        session.add(
            TierImageModelLimit(
                tier_id=tier.id,
                image_model="gemini-3.1-flash-image-preview",
                monthly_requests=-1,
            )
        )
        session.add(UserSubscription(user_id=user.id, tier_id=tier.id, status=SubscriptionStatus.active))
        session.add(
            ImageQualityPricing(
                image_model="gemini-3.1-flash-image-preview",
                quality="low",
                credit_cost=99.0,
                is_active=True,
            )
        )
        await session.commit()

        legacy = await get_image_quality_pricing(session, "gemini-3.1-flash-image-preview", "low")
        canonical = await get_image_quality_pricing(session, "gemini-3.1-flash-image-preview", "1k")
        usage = await get_image_usage(session, user)

    await engine.dispose()

    assert legacy is None
    assert canonical is not None
    assert canonical.credit_cost == 2.0

    model_usage = next(
        model for model in usage.models if model.model == "gemini-3.1-flash-image-preview"
    )
    assert [resolution.resolution for resolution in model_usage.resolutions] == ["512", "1k", "2k"]
