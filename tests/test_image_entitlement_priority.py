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
    UsagePack,
    UsagePackImageModelLimit,
    UsagePackSource,
    UsagePackStatus,
    UserSubscription,
    UserUsagePack,
)
from app.services.subscription_check.entitlements import select_image_entitlement


async def _prepare_daily_tier_and_pack(session: AsyncSession, telegram_id: int):
    user = m.AppUser(telegram_id=telegram_id)
    session.add(user)
    await session.flush()

    tier = (await session.exec(select(SubscriptionTier).where(SubscriptionTier.name == "free"))).first()
    assert tier is not None

    session.add(TierImageModelLimit(tier_id=tier.id, image_model="gpt-image-1.5", monthly_requests=-1))
    session.add(TierImageQualityLimit(tier_id=tier.id, quality="low"))
    session.add(UserSubscription(user_id=user.id, tier_id=tier.id, status=SubscriptionStatus.active))

    pack = UsagePack(
        name=f"pack-{telegram_id}",
        name_ru=f"pack-ru-{telegram_id}",
        description="pack",
        description_ru="pack",
        price_cents=100,
        is_active=True,
        is_public=False,
        index=0,
    )
    session.add(pack)
    await session.flush()

    session.add(
        UsagePackImageModelLimit(
            pack_id=pack.id,
            image_model="gpt-image-1.5",
            credit_amount=100,
        )
    )
    user_pack = UserUsagePack(
        user_id=user.id,
        pack_id=pack.id,
        source=UsagePackSource.paid,
        status=UsagePackStatus.active,
    )
    session.add(user_pack)
    await session.commit()
    await session.refresh(user_pack)
    return user, tier, user_pack


@pytest.mark.asyncio
async def test_image_daily_tier_is_prioritized_before_pack():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user, tier, _ = await _prepare_daily_tier_and_pack(session, telegram_id=721000401)
        entitlement = await select_image_entitlement(session, user.id, "gpt-image-1.5", "low")

    await engine.dispose()

    assert entitlement["allowed"] is True
    assert entitlement["kind"] == "tier"
    assert entitlement["tier_id"] == str(tier.id)
    assert entitlement["usage_pack_id"] is None


@pytest.mark.asyncio
async def test_image_daily_tier_throttled_falls_back_to_pack():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user, tier, user_pack = await _prepare_daily_tier_and_pack(session, telegram_id=721000402)

        # free tier in tests has daily_image_limit=2 -> burst bucket capacity is 10 credits.
        # Add 11 consumed requests to force pacing throttle for the next request.
        for _ in range(11):
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

        entitlement = await select_image_entitlement(session, user.id, "gpt-image-1.5", "low")

    await engine.dispose()

    assert entitlement["allowed"] is True
    assert entitlement["kind"] == "pack"
    assert entitlement["usage_pack_id"] == str(user_pack.id)


@pytest.mark.asyncio
async def test_image_daily_tier_throttled_without_fallback_returns_pacing():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = m.AppUser(telegram_id=721000403)
        session.add(user)
        await session.flush()

        tier = (await session.exec(select(SubscriptionTier).where(SubscriptionTier.name == "free"))).first()
        assert tier is not None

        session.add(TierImageModelLimit(tier_id=tier.id, image_model="gpt-image-1.5", monthly_requests=-1))
        session.add(TierImageQualityLimit(tier_id=tier.id, quality="low"))
        session.add(UserSubscription(user_id=user.id, tier_id=tier.id, status=SubscriptionStatus.active))

        for _ in range(11):
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

        entitlement = await select_image_entitlement(session, user.id, "gpt-image-1.5", "low")

    await engine.dispose()

    assert entitlement["allowed"] is False
    assert entitlement["throttle_reason"] == "pacing"
    assert entitlement["wait_time"] is not None
