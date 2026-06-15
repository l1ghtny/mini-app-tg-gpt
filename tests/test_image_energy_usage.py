import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.user_usage_helpers import get_image_energy_usage
from app.db import models as m
from app.db.subscription_tiers import (
    SubscriptionStatus,
    SubscriptionTier,
    TierImageModelLimit,
    TierImageQualityLimit,
    UserSubscription,
)


@pytest.mark.asyncio
async def test_image_energy_usage_reports_saved_and_used():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = m.AppUser(telegram_id=721000601)
        session.add(user)
        await session.flush()

        tier = (await session.exec(select(SubscriptionTier).where(SubscriptionTier.name == "free"))).first()
        assert tier is not None
        tier.daily_image_energy = 2
        session.add(tier)

        session.add(TierImageModelLimit(tier_id=tier.id, image_model="gpt-image-1.5", monthly_requests=-1))
        session.add(TierImageQualityLimit(tier_id=tier.id, quality="low"))
        session.add(
            UserSubscription(
                user_id=user.id,
                tier_id=tier.id,
                status=SubscriptionStatus.active,
                started_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=5),
            )
        )

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

        response = await get_image_energy_usage(session, user)

    await engine.dispose()

    assert response.status == "active"
    assert len(response.sources) == 1
    source = response.sources[0]
    assert source.daily_energy == 2
    assert source.max_energy == 10
    assert source.available_energy == 7
    assert source.saved_energy == 5
    assert source.used_energy == 3


@pytest.mark.asyncio
async def test_image_energy_usage_none_for_recurring_tiers_without_daily_energy():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = m.AppUser(telegram_id=721000602)
        session.add(user)
        await session.flush()

        tier = (await session.exec(select(SubscriptionTier).where(SubscriptionTier.name == "free"))).first()
        assert tier is not None
        tier.daily_image_energy = 0
        tier.is_recurring = True
        session.add(tier)

        session.add(TierImageModelLimit(tier_id=tier.id, image_model="gpt-image-1.5", monthly_requests=20))
        session.add(TierImageQualityLimit(tier_id=tier.id, quality="low"))
        session.add(
            UserSubscription(
                user_id=user.id,
                tier_id=tier.id,
                status=SubscriptionStatus.active,
                started_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
        await session.commit()

        response = await get_image_energy_usage(session, user)

    await engine.dispose()

    assert response.status == "none"
    assert response.sources == []


@pytest.mark.asyncio
async def test_image_energy_usage_recurring_daily_budget_starts_with_one_day_balance():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = m.AppUser(telegram_id=721000603)
        session.add(user)
        await session.flush()

        tier = (await session.exec(select(SubscriptionTier).where(SubscriptionTier.name == "free"))).first()
        assert tier is not None
        tier.monthly_images = 80
        tier.daily_image_energy = 80
        tier.is_recurring = True
        session.add(tier)

        session.add(TierImageModelLimit(tier_id=tier.id, image_model="gpt-image-1.5", monthly_requests=-1))
        session.add(TierImageQualityLimit(tier_id=tier.id, quality="low"))
        session.add(
            UserSubscription(
                user_id=user.id,
                tier_id=tier.id,
                status=SubscriptionStatus.active,
                started_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )

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

        response = await get_image_energy_usage(session, user)

    await engine.dispose()

    assert response.status == "active"
    assert len(response.sources) == 1
    source = response.sources[0]
    assert source.daily_energy == 80
    assert source.max_energy == 400
    assert source.available_energy == 77
    assert source.saved_energy == 0
    assert source.used_energy == 3


@pytest.mark.asyncio
async def test_image_energy_usage_recurring_daily_budget_accumulates_five_day_burst_over_time():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = m.AppUser(telegram_id=721000604)
        session.add(user)
        await session.flush()

        tier = (await session.exec(select(SubscriptionTier).where(SubscriptionTier.name == "free"))).first()
        assert tier is not None
        tier.monthly_images = 80
        tier.daily_image_energy = 80
        tier.is_recurring = True
        session.add(tier)

        session.add(TierImageModelLimit(tier_id=tier.id, image_model="gpt-image-1.5", monthly_requests=-1))
        session.add(TierImageQualityLimit(tier_id=tier.id, quality="low"))
        session.add(
            UserSubscription(
                user_id=user.id,
                tier_id=tier.id,
                status=SubscriptionStatus.active,
                started_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=5),
            )
        )

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

        response = await get_image_energy_usage(session, user)

    await engine.dispose()

    assert response.status == "active"
    assert len(response.sources) == 1
    source = response.sources[0]
    assert source.daily_energy == 80
    assert source.max_energy == 400
    assert source.available_energy == 397
    assert source.saved_energy == 317
    assert source.used_energy == 3
