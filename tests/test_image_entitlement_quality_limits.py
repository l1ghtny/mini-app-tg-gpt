import os

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
from app.services.subscription_check.entitlements import select_image_entitlement


@pytest.mark.asyncio
async def test_quality_allow_list_empty_means_restricted():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = m.AppUser(telegram_id=721000301)
        session.add(user)
        await session.flush()

        tier = (
            await session.exec(
                select(SubscriptionTier).where(SubscriptionTier.name == "free")
            )
        ).first()
        assert tier is not None

        session.add(
            TierImageModelLimit(
                tier_id=tier.id,
                image_model="gpt-image-1.5",
                monthly_requests=300,
            )
        )
        session.add(
            UserSubscription(
                user_id=user.id,
                tier_id=tier.id,
                status=SubscriptionStatus.active,
            )
        )
        await session.commit()

        entitlement = await select_image_entitlement(session, user.id, "gpt-image-1.5", "low")

    await engine.dispose()

    assert entitlement["allowed"] is False
    assert entitlement["throttle_reason"] == "quality_restricted"


@pytest.mark.asyncio
async def test_quality_allow_list_allows_configured_quality():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = m.AppUser(telegram_id=721000302)
        session.add(user)
        await session.flush()

        tier = (
            await session.exec(
                select(SubscriptionTier).where(SubscriptionTier.name == "free")
            )
        ).first()
        assert tier is not None

        session.add(
            TierImageModelLimit(
                tier_id=tier.id,
                image_model="gpt-image-1.5",
                monthly_requests=300,
            )
        )
        session.add(TierImageQualityLimit(tier_id=tier.id, quality="low"))
        session.add(
            UserSubscription(
                user_id=user.id,
                tier_id=tier.id,
                status=SubscriptionStatus.active,
            )
        )
        await session.commit()

        entitlement = await select_image_entitlement(session, user.id, "gpt-image-1.5", "low")

    await engine.dispose()

    assert entitlement["allowed"] is True
    assert entitlement["throttle_reason"] is None
    assert entitlement["kind"] == "tier"
