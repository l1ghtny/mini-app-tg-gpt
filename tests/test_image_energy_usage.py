import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api import user_usage_helpers
from app.api.user_usage_helpers import get_image_energy_usage, get_image_usage
from app.db import models as m
from app.db.models import ImageQualityPricing
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



@pytest.mark.asyncio
async def test_image_usage_reuses_preloaded_energy_snapshot_for_pacing(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = m.AppUser(telegram_id=721000605)
        session.add(user)
        await session.flush()

        tier = (await session.exec(select(SubscriptionTier).where(SubscriptionTier.name == "free"))).first()
        assert tier is not None
        tier.daily_image_energy = 2
        tier.monthly_images = 20
        tier.is_recurring = True
        session.add(tier)

        session.add(TierImageModelLimit(tier_id=tier.id, image_model="gpt-image-1.5", monthly_requests=-1))
        session.add(TierImageQualityLimit(tier_id=tier.id, quality="low"))
        session.add(
            UserSubscription(
                user_id=user.id,
                tier_id=tier.id,
                status=SubscriptionStatus.active,
                started_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1),
            )
        )

        existing_pricing = (
            await session.exec(
                select(ImageQualityPricing).where(
                    ImageQualityPricing.image_model == "gpt-image-1.5",
                    ImageQualityPricing.quality == "low",
                    ImageQualityPricing.is_active == True,
                )
            )
        ).first()
        if existing_pricing is None:
            session.add(
                ImageQualityPricing(
                    image_model="gpt-image-1.5",
                    quality="low",
                    credit_cost=1.0,
                    is_active=True,
                )
            )

        await session.commit()

        async def _unexpected_check_image_pacing(*args, **kwargs):
            raise AssertionError("get_image_usage should reuse the preloaded energy snapshot")

        monkeypatch.setattr(user_usage_helpers, "check_image_pacing", _unexpected_check_image_pacing)

        response = await get_image_usage(session, user)

    await engine.dispose()

    assert response.status == "active"
    assert len(response.models) == 1
    assert response.models[0].resolutions
    pacing = response.models[0].resolutions[0].sources[0].pacing
    assert pacing is not None
    assert pacing.wait_seconds >= 0
