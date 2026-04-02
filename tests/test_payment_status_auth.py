import os
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api import payment_helpers
from app.db.models import AppUser, Payment, PaymentProductType


@pytest.mark.asyncio
async def test_payment_status_requires_payment_owner():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url

    engine = create_async_engine(test_db_url, future=True, echo=False)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        owner = AppUser(telegram_id=721000100)
        other = AppUser(telegram_id=721000101)
        session.add(owner)
        session.add(other)
        await session.commit()
        await session.refresh(owner)
        await session.refresh(other)

        payment = Payment(
            user_id=owner.id,
            tier_name=f"tier-{uuid.uuid4()}",
            amount=49000,
            tbank_status="NEW",
            product_type=PaymentProductType.subscription,
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        status = await payment_helpers.get_payment_status(session, payment.id, owner)
        assert status.id == str(payment.id)
        assert status.status == "NEW"

    async with AsyncSession(engine, expire_on_commit=False) as session:
        with pytest.raises(HTTPException) as exc:
            await payment_helpers.get_payment_status(session, payment.id, other)
        assert exc.value.status_code == 404
