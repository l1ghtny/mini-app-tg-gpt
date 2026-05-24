import os
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.payment_helpers import init_subscription_payment
from app.db.models import AppUser, Payment
from app.db.subscription_tiers import GeneralDiscount, SubscriptionTier
from app.schemas.subscriptions import InitPaymentRequest
import app.api.payment_helpers as payment_helpers


class _FakeTbankService:
    async def init_payment(self, **_kwargs):
        return "https://pay.example.test/checkout", "TBANK_FAKE_1"


@pytest.mark.asyncio
async def test_init_subscription_payment_applies_stackable_general_discounts(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)
    monkeypatch.setattr(payment_helpers, "tbank_service", _FakeTbankService(), raising=True)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=791000001)
        session.add(user)

        tier = SubscriptionTier(
            name="Basic",
            name_ru="Basic",
            description="",
            description_ru="",
            price_cents=100,
            is_active=True,
            is_public=True,
            is_recurring=True,
        )
        session.add(tier)
        await session.commit()
        await session.refresh(user)
        await session.refresh(tier)

        now = datetime.utcnow().replace(microsecond=0)
        d1 = GeneralDiscount(
            code="FIRST20",
            type="first_purchase",
            percent_off=20,
            applies_to_tiers=["basic"],
            conditions={"no_prior_paid_sub": True},
            starts_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=7),
            is_active=True,
            stackable=True,
        )
        d2 = GeneralDiscount(
            code="MAY10",
            type="seasonal",
            percent_off=10,
            applies_to_tiers=["basic"],
            starts_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=7),
            is_active=True,
            stackable=True,
        )
        session.add(d1)
        session.add(d2)
        await session.commit()

        result = await init_subscription_payment(
            session=session,
            user=user,
            payload=InitPaymentRequest(
                tier_name="Basic",
                email="user@example.com",
                discount_codes=["FIRST20", "MAY10"],
            ),
        )
        assert result.payment_url

        payment = (await session.exec(select(Payment).where(Payment.user_id == user.id))).first()

        assert payment is not None
        # base=100*100=10000 -> 20% then 10% => 7200
        assert payment.amount == 7200

    await engine.dispose()
