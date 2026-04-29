import os
import uuid

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db import models as m
from app.db.subscription_tiers import (
    SubscriptionStatus,
    SubscriptionTier,
    TierImageModelLimit,
    TierImageQualityLimit,
    UserSubscription,
)
from app.services.subscription_check.entitlements import list_image_entitlements


@pytest.mark.asyncio
async def test_image_credits_are_shared_across_models_for_tier():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = m.AppUser(telegram_id=721000501)
        session.add(user)
        await session.flush()

        tier = (
            await session.exec(
                select(SubscriptionTier).where(SubscriptionTier.name == "free")
            )
        ).first()
        assert tier is not None

        # Make this tier monthly-only so it uses pool caps in UI/API.
        tier.daily_image_limit = 0
        tier.is_recurring = True
        session.add(tier)

        session.add(TierImageModelLimit(tier_id=tier.id, image_model="gpt-image-1.5", monthly_requests=20))
        session.add(TierImageModelLimit(tier_id=tier.id, image_model="gpt-image-2", monthly_requests=20))
        session.add(TierImageQualityLimit(tier_id=tier.id, quality="low"))
        session.add(
            UserSubscription(
                user_id=user.id,
                tier_id=tier.id,
                status=SubscriptionStatus.active,
            )
        )

        # Spend 3 credits on gpt-image-1.5; gpt-image-2 should see the same shared pool.
        for _ in range(3):
            session.add(
                m.RequestLedger(
                    user_id=user.id,
                    tier_id=tier.id,
                    usage_pack_id=None,
                    request_id=str(uuid.uuid4()),
                    model_name="gpt-image-1.5",
                    feature="image",
                    cost=1.0,
                    state=m.State.consumed,
                )
            )

        await session.commit()

        breakdown_15 = await list_image_entitlements(session, user.id, "gpt-image-1.5")
        breakdown_2 = await list_image_entitlements(session, user.id, "gpt-image-2")

    await engine.dispose()

    # Shared pool: both models should report identical remaining credits.
    assert breakdown_15["total_remaining_credits"] == 17
    assert breakdown_2["total_remaining_credits"] == 17


@pytest.mark.asyncio
async def test_daily_limited_tier_reports_infinite_image_remaining():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = m.AppUser(telegram_id=721000502)
        session.add(user)
        await session.flush()

        tier = (
            await session.exec(
                select(SubscriptionTier).where(SubscriptionTier.name == "free")
            )
        ).first()
        assert tier is not None

        # Any positive daily limit should show this tier as infinite in remaining display.
        tier.daily_image_limit = 5
        session.add(tier)

        session.add(TierImageModelLimit(tier_id=tier.id, image_model="gpt-image-1.5", monthly_requests=20))
        session.add(TierImageQualityLimit(tier_id=tier.id, quality="low"))
        session.add(
            UserSubscription(
                user_id=user.id,
                tier_id=tier.id,
                status=SubscriptionStatus.active,
            )
        )
        await session.commit()

        breakdown = await list_image_entitlements(session, user.id, "gpt-image-1.5")

    await engine.dispose()

    assert breakdown["total_remaining_credits"] == -1
