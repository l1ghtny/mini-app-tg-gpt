import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.user_usage_helpers import get_text_usage
from app.db import models as m
from app.db.subscription_tiers import SubscriptionStatus, SubscriptionTier, TierModelLimit, UserSubscription
from app.services.subscription_check.entitlements import select_text_entitlement


@pytest.mark.asyncio
async def test_text_usage_daily_limits_reset_at_utc_midnight():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = m.AppUser(telegram_id=721000701)
        session.add(user)
        await session.flush()

        tier = (await session.exec(select(SubscriptionTier).where(SubscriptionTier.name == "free"))).one()
        limit = (
            await session.exec(
                select(TierModelLimit).where(
                    TierModelLimit.tier_id == tier.id,
                    TierModelLimit.model_name == "gpt-5.4-nano",
                )
            )
        ).one()
        limit.monthly_requests = 100
        limit.daily_requests = 5
        session.add(limit)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add(
            UserSubscription(
                user_id=user.id,
                tier_id=tier.id,
                status=SubscriptionStatus.active,
                started_at=now - timedelta(days=1),
            )
        )
        session.add(
            m.RequestLedger(
                user_id=user.id,
                tier_id=tier.id,
                usage_pack_id=None,
                request_id=str(uuid.uuid4()),
                model_name="gpt-5.4-nano",
                feature="text",
                cost=1.0,
                state=m.State.consumed,
                created_at=now - timedelta(hours=1),
            )
        )
        session.add(
            m.RequestLedger(
                user_id=user.id,
                tier_id=tier.id,
                usage_pack_id=None,
                request_id=str(uuid.uuid4()),
                model_name="gpt-5.4-nano",
                feature="text",
                cost=1.0,
                state=m.State.consumed,
                created_at=now - timedelta(days=1, minutes=5),
            )
        )
        await session.commit()

        response = await get_text_usage(session, user)

    await engine.dispose()

    assert response.status == "active"
    nano_model = next(model for model in response.models if model.model == "gpt-5.4-nano")
    assert nano_model.display_name == "Fast"
    assert nano_model.display_name_ru == "Быстрый"
    assert nano_model.bucket_models == ["gpt-5.4-nano", "gemini-3.1-flash-lite"]
    assert nano_model.total_remaining == 4
    assert nano_model.next_reset_at is not None
    assert nano_model.next_reset_at.utcoffset() == timedelta(0)
    assert nano_model.next_reset_at.hour == 0
    assert nano_model.next_reset_at.minute == 0
    assert nano_model.selected is not None
    assert nano_model.selected.cap == 5
    assert nano_model.selected.used == 1
    assert nano_model.selected.remaining == 4
    assert nano_model.selected.next_reset_at is not None
    assert nano_model.selected.next_reset_at.utcoffset() == timedelta(0)
    assert nano_model.selected.next_reset_at.hour == 0
    assert nano_model.selected.next_reset_at.minute == 0


@pytest.mark.asyncio
async def test_text_usage_merges_openai_and_google_pair_into_shared_bucket():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = m.AppUser(telegram_id=721000702)
        session.add(user)
        await session.flush()

        tier = (await session.exec(select(SubscriptionTier).where(SubscriptionTier.name == "free"))).one()
        openai_limit = (
            await session.exec(
                select(TierModelLimit).where(
                    TierModelLimit.tier_id == tier.id,
                    TierModelLimit.model_name == "gpt-5.4-nano",
                )
            )
        ).one()
        google_limit = TierModelLimit(
            tier_id=tier.id,
            model_name="gemini-3.1-flash-lite",
            monthly_requests=25,
            daily_requests=0,
        )
        openai_limit.monthly_requests = 25
        session.add(openai_limit)
        session.add(google_limit)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add(
            UserSubscription(
                user_id=user.id,
                tier_id=tier.id,
                status=SubscriptionStatus.active,
                started_at=now - timedelta(days=1),
            )
        )
        session.add(
            m.RequestLedger(
                user_id=user.id,
                tier_id=tier.id,
                usage_pack_id=None,
                request_id=str(uuid.uuid4()),
                model_name="gpt-5.4-nano",
                feature="text",
                cost=1.0,
                state=m.State.consumed,
                created_at=now - timedelta(hours=2),
            )
        )
        session.add(
            m.RequestLedger(
                user_id=user.id,
                tier_id=tier.id,
                usage_pack_id=None,
                request_id=str(uuid.uuid4()),
                model_name="gemini-3.1-flash-lite",
                feature="text",
                cost=1.0,
                state=m.State.consumed,
                created_at=now - timedelta(hours=1),
            )
        )
        await session.commit()

        response = await get_text_usage(session, user)
        google_entitlement = await select_text_entitlement(session, user.id, "gemini-3.1-flash-lite")

    await engine.dispose()

    bucket_rows = [model for model in response.models if model.model == "gpt-5.4-nano"]
    assert len(bucket_rows) == 1
    bucket = bucket_rows[0]
    assert bucket.display_name == "Fast"
    assert bucket.display_name_ru == "Быстрый"
    assert bucket.bucket_models == ["gpt-5.4-nano", "gemini-3.1-flash-lite"]
    assert bucket.total_remaining == 23
    assert bucket.selected is not None
    assert bucket.selected.used == 2
    assert bucket.selected.remaining == 23
    assert all(model.model != "gemini-3.1-flash-lite" for model in response.models)

    assert google_entitlement["used"] == 2
    assert google_entitlement["remaining"] == 23
