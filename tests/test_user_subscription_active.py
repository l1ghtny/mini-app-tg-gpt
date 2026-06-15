import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.user_subscription import get_active_subscription
from app.api.user_subscription_helpers import cancel_subscription
from app.db.models import AppUser, Payment, PaymentMethod, PaymentProductType
from app.db.subscription_tiers import (
    AccessCode,
    SubscriptionTier,
    SubscriptionStatus,
    UserSubscription,
    UserTierDiscount,
)


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
        paid_name = f"advanced-{uuid.uuid4()}"

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
            description="advanced",
            description_ru="advanced",
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
    assert len(result.active_subscriptions) == 2
    assert result.primary_subscription_id is not None
    assert result.active_subscriptions[0].tier_name == paid_name
    assert result.active_subscriptions[0].tier_slug == paid_name
    assert result.active_subscriptions[0].tier_rank == 10
    assert result.active_subscriptions[0].tier_price == 1000
    assert "T" in result.active_subscriptions[0].started_at
    assert datetime.fromisoformat(result.active_subscriptions[0].started_at)
    assert datetime.fromisoformat(result.active_subscriptions[0].expires_at)
    assert {sub.tier_name for sub in result.active_subscriptions} == {free_name, paid_name}


@pytest.mark.asyncio
async def test_active_subscription_recurring_without_expiry_gets_fallback_expiry():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url

    engine = create_async_engine(test_db_url, future=True, echo=False)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=721000003)
        session.add(user)
        await session.commit()
        await session.refresh(user)

        paid_name = f"advanced-{uuid.uuid4()}"
        paid_tier = SubscriptionTier(
            name=paid_name,
            name_ru=paid_name,
            description="advanced",
            description_ru="advanced",
            price_cents=1000,
            index=10,
            is_recurring=True,
        )
        session.add(paid_tier)
        await session.commit()
        await session.refresh(paid_tier)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        recurring_sub = UserSubscription(
            user_id=user.id,
            tier_id=paid_tier.id,
            status=SubscriptionStatus.active,
            started_at=now - timedelta(days=2),
            expires_at=None,
        )
        session.add(recurring_sub)
        await session.commit()

    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await get_active_subscription(session=session, user=user)

    assert len(result.active_subscriptions) == 1
    assert result.active_subscriptions[0].tier_name == paid_name
    assert result.active_subscriptions[0].tier_slug == paid_name
    assert result.active_subscriptions[0].tier_rank == 10
    assert result.active_subscriptions[0].expires_at is not None
    assert datetime.fromisoformat(result.active_subscriptions[0].expires_at)


@pytest.mark.asyncio
async def test_active_subscription_free_recurring_without_expiry_keeps_null_expiry():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url

    engine = create_async_engine(test_db_url, future=True, echo=False)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=7210000031)
        session.add(user)
        await session.commit()
        await session.refresh(user)

        tier_name = f"welcoming-{uuid.uuid4()}"
        tier = SubscriptionTier(
            name=tier_name,
            name_ru=tier_name,
            description="free recurring",
            description_ru="free recurring",
            price_cents=0,
            index=1,
            is_recurring=True,
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
                started_at=now - timedelta(days=2),
                expires_at=None,
            )
        )
        await session.commit()

    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await get_active_subscription(session=session, user=user)

    await engine.dispose()

    assert len(result.active_subscriptions) == 1
    assert result.active_subscriptions[0].tier_name == tier_name
    assert result.active_subscriptions[0].expires_at is None


@pytest.mark.asyncio
async def test_active_subscription_includes_discounts_and_first_purchase_flag():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url

    engine = create_async_engine(test_db_url, future=True, echo=False)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=721000004)
        session.add(user)
        await session.commit()
        await session.refresh(user)

        tier_name = f"starter-{uuid.uuid4()}"
        tier = SubscriptionTier(
            name=tier_name,
            name_ru=tier_name,
            description="starter",
            description_ru="starter",
            price_cents=0,
            index=1,
            is_recurring=False,
        )
        session.add(tier)
        await session.commit()
        await session.refresh(tier)

        session.add(
            UserSubscription(
                user_id=user.id,
                tier_id=tier.id,
                status=SubscriptionStatus.active,
                started_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1),
            )
        )
        access_code = AccessCode(code=f"DISC-{uuid.uuid4()}", tier_id=tier.id)
        session.add(access_code)
        await session.commit()
        await session.refresh(access_code)

        session.add(
            UserTierDiscount(
                user_id=user.id,
                tier_id=tier.id,
                discount_percent=25,
                valid_until=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=7),
                access_code_id=access_code.id,
            )
        )
        session.add(
            Payment(
                user_id=user.id,
                tier_name=tier.name,
                product_type=PaymentProductType.subscription,
                amount=1000,
                tbank_status="CONFIRMED",
            )
        )
        await session.commit()

    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await get_active_subscription(session=session, user=user)

    assert result.first_purchase_available is False
    assert len(result.discounts) == 1
    assert result.discounts[0].percent_off == 25
    assert result.discounts[0].stackable is True
    assert result.discounts[0].code is not None
    assert result.discounts[0].applies_to == [tier_name]


@pytest.mark.asyncio
async def test_active_subscription_reports_grace_and_default_method():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url

    engine = create_async_engine(test_db_url, future=True, echo=False)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=721000005)
        session.add(user)
        await session.commit()
        await session.refresh(user)

        paid_name = f"advanced-{uuid.uuid4()}"
        tier = SubscriptionTier(
            name=paid_name,
            name_ru=paid_name,
            description="advanced",
            description_ru="advanced",
            price_cents=1000,
            index=10,
            is_recurring=True,
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
                expires_at=now + timedelta(days=29),
                renewal_grace_until=now + timedelta(hours=12),
                last_renewal_attempt_at=now - timedelta(hours=1),
                last_renewal_failure_reason="missing_method",
                auto_renew_enabled=True,
            )
        )
        method = PaymentMethod(
            user_id=user.id,
            rebill_id="REBILL-GRACE-1",
            type="card",
            card_type="Visa",
            pan="**** 4242",
            exp_date="1229",
            status="active",
            is_default=True,
            bound_at=now - timedelta(days=3),
        )
        session.add(method)
        await session.commit()
        await session.refresh(method)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await get_active_subscription(session=session, user=user)

    await engine.dispose()

    assert len(result.active_subscriptions) == 1
    subscription = result.active_subscriptions[0]
    assert subscription.renewal_state == "grace"
    assert subscription.default_payment_method_id == str(method.id)
    assert subscription.last_renewal_failure_reason == "missing_method"
    assert subscription.renewal_grace_until is not None


@pytest.mark.asyncio
async def test_cancel_subscription_disables_auto_renew_without_detaching_methods():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url

    engine = create_async_engine(test_db_url, future=True, echo=False)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=721000006)
        session.add(user)
        await session.commit()
        await session.refresh(user)

        tier_name = f"advanced-{uuid.uuid4()}"
        tier = SubscriptionTier(
            name=tier_name,
            name_ru=tier_name,
            description="advanced",
            description_ru="advanced",
            price_cents=1000,
            index=10,
            is_recurring=True,
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
                expires_at=now + timedelta(days=29),
                auto_renew_enabled=True,
            )
        )
        method = PaymentMethod(
            user_id=user.id,
            rebill_id="REBILL-CANCEL-1",
            type="card",
            card_type="Visa",
            pan="**** 4242",
            exp_date="1229",
            status="active",
            is_default=True,
            bound_at=now - timedelta(days=5),
        )
        session.add(method)
        await session.commit()
        await session.refresh(method)

        result = await cancel_subscription(session, user, BackgroundTasks())
        assert result.status == "success"

        sub = (await session.exec(select(UserSubscription).where(UserSubscription.user_id == user.id))).one()
        saved_method = (await session.exec(select(PaymentMethod).where(PaymentMethod.user_id == user.id))).one()

    await engine.dispose()

    assert sub.auto_renew_enabled is False
    assert saved_method.status == "active"
    assert saved_method.is_default is True
