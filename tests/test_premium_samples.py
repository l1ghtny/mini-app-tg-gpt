import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import AppUser
from app.db.subscription_tiers import SubscriptionStatus, SubscriptionTier, UserSubscription
from app.services.premium_samples import assert_premium_sample_can_be_used


@pytest.mark.asyncio
async def test_premium_sample_is_rejected_for_active_free_subscription():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=722100001)
        session.add(user)
        await session.commit()
        await session.refresh(user)

        tier = SubscriptionTier(
            name="welcoming-bonus-free-test",
            name_ru="welcoming-bonus-free-test",
            description="Free welcome tier",
            description_ru="Free welcome tier",
            price_cents=0,
            index=0,
            is_recurring=False,
            is_public=False,
            is_active=True,
        )
        session.add(tier)
        await session.commit()
        await session.refresh(tier)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add(
            UserSubscription(
                user_id=user.id,
                tier_id=tier.id,
                status=SubscriptionStatus.active,
                started_at=now - timedelta(days=1),
                expires_at=now + timedelta(days=30),
                auto_renew_enabled=False,
            )
        )
        await session.commit()

        with pytest.raises(HTTPException) as exc_info:
            await assert_premium_sample_can_be_used(
                session,
                user_id=user.id,
                kind="flagship_text",
                model="gpt-5.5",
            )

    await engine.dispose()

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"] == "premium_sample_not_applicable"
    assert exc_info.value.detail["reason"] == "already_has_active_subscription"
