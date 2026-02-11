import uuid
import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

from app.db import models as m
from app.db.subscription_tiers import UsagePack, UserUsagePack, UsagePackSource
from app.api import payment_helpers
from app.schemas.subscriptions import MockUsagePackPurchaseRequest
from fastapi import BackgroundTasks

@pytest.mark.asyncio
async def test_mock_usage_pack_purchase(monkeypatch):
    # Setup DB
    import os
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    # Create User and UsagePack
    async with AsyncSession(engine, expire_on_commit=False) as s:
        user = m.AppUser(telegram_id=123456789)
        s.add(user)
        
        pack = UsagePack(
            name="test-pack",
            price_cents=100,
            is_active=True,
            is_public=True,
            index=1
        )
        s.add(pack)
        await s.commit()
        await s.refresh(user)
        await s.refresh(pack)
        
        user_id = str(user.id)
        pack_id = str(pack.id)

    # Call the mock function
    async with AsyncSession(engine, expire_on_commit=False) as s:
        payload = MockUsagePackPurchaseRequest(user_id=user_id, pack_id=pack_id)
        background_tasks = BackgroundTasks()
        
        response = await payment_helpers.mock_usage_pack_purchase(s, background_tasks, payload)
        assert response.status_code == 200

    # Verify the result
    async with AsyncSession(engine, expire_on_commit=False) as s:
        # Check Payment
        payment_query = select(m.Payment).where(m.Payment.user_id == uuid.UUID(user_id))
        payment = (await s.exec(payment_query)).first()
        assert payment is not None
        assert payment.tbank_status == "CONFIRMED"
        assert payment.product_type == m.PaymentProductType.usage_pack
        assert str(payment.pack_id) == pack_id

        # Check UserUsagePack
        user_pack_query = select(UserUsagePack).where(UserUsagePack.user_id == uuid.UUID(user_id))
        user_pack = (await s.exec(user_pack_query)).first()
        assert user_pack is not None
        assert str(user_pack.pack_id) == pack_id
        assert user_pack.source == UsagePackSource.paid
        assert user_pack.payment_id == payment.id
