import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.user_subscription import get_active_subscription
from app.db.models import AppUser
from app.db.subscription_tiers import SubscriptionTier, UserSubscription, SubscriptionStatus


@pytest.mark.asyncio
async def test_active_subscription_prefers_paid_over_free():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url

    engine = create_async_engine(test_db_url, future=True, echo=False)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=721000002)
        session.add(user)
        await session.commit()
        await session.refresh(user)

        free_name = f"free-{uuid.uuid4()}"
        paid_name = f"pro-{uuid.uuid4()}"

        free_tier = SubscriptionTier(
            name=free_name,
            name_ru=free_name,
            description="free",
            description_ru="free",
            price_cents=0,
            index=0,
            is_recurring=False,
        )
        paid_tier = SubscriptionTier(
            name=paid_name,
            name_ru=paid_name,
            description="pro",
            description_ru="pro",
            price_cents=1000,
            index=10,
            is_recurring=True,
        )
        session.add(free_tier)
        session.add(paid_tier)
        await session.commit()
        await session.refresh(free_tier)
        await session.refresh(paid_tier)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        free_sub = UserSubscription(
            user_id=user.id,
            tier_id=free_tier.id,
            status=SubscriptionStatus.active,
            started_at=now - timedelta(days=10),
            expires_at=None,
        )
        paid_sub = UserSubscription(
            user_id=user.id,
            tier_id=paid_tier.id,
            status=SubscriptionStatus.active,
            started_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=30),
        )
        session.add(free_sub)
        session.add(paid_sub)
        await session.commit()

    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await get_active_subscription(session=session, user=user)
    print(result.tier_name, result.tier_price)

    assert result.tier_name == paid_name
    assert result.tier_price == 1000
