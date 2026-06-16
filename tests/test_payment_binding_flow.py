import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

import app.api.payment_helpers as payment_helpers
from app.db.models import AppUser, Payment, PaymentBindingSession, PaymentMethod, PaymentProductType
from app.db.subscription_tiers import SubscriptionStatus, SubscriptionTier, UserSubscription
from app.schemas.subscriptions import BoundSubscriptionChargeRequest, SubscriptionBindingInitRequest


class _FakeTbankService:
    def __init__(self):
        self.last_init_kwargs = None

    async def add_card(self, **_kwargs):
        return {
            "Success": True,
            "RequestKey": "BIND-CARD-1",
            "PaymentURL": "https://bind.example.test/card",
        }

    async def get_add_card_state(self, _request_key: str):
        return {
            "Success": True,
            "Status": "COMPLETED",
            "RebillId": "REBILL-123",
            "Pan": "**** 4242",
            "CardType": "Visa",
            "ExpDate": "1229",
        }

    async def add_account_qr(self, **_kwargs):
        return {
            "Success": True,
            "RequestKey": "BIND-SBP-1",
            "Data": "000201010212...",
            "DataType": "PAYLOAD",
        }

    async def get_add_account_qr_state(self, _request_key: str):
        return {
            "Success": True,
            "Status": "ACTIVE",
            "AccountToken": "ACCOUNT-TOKEN-1",
            "Phone": "+79990001122",
        }

    async def init_payment(self, **_kwargs):
        self.last_init_kwargs = dict(_kwargs)
        return "https://pay.example.test/charge", "TBANK-PAY-1"

    async def charge(self, *_args, **_kwargs):
        return {"Success": True}

    async def cancel_payment(self, payment_id: str, amount: int | None = None):
        return {
            "Success": True,
            "PaymentId": payment_id,
            "Amount": amount,
            "Status": "REFUNDED",
        }


class _FailingCardStatusTbankService(_FakeTbankService):
    async def get_add_card_state(self, _request_key: str):
        raise Exception("TBank GetAddCardState Failed: Could not bind card. Internal error.")


@pytest.mark.asyncio
async def test_subscription_binding_does_not_activate_until_charge(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)
    monkeypatch.setattr(payment_helpers, "tbank_service", _FakeTbankService(), raising=True)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=791000101)
        tier = SubscriptionTier(
            name="Advanced",
            name_ru="Advanced",
            description="",
            description_ru="",
            price_cents=100,
            is_active=True,
            is_public=True,
            is_recurring=True,
        )
        session.add(user)
        session.add(tier)
        await session.commit()
        await session.refresh(user)

        binding = await payment_helpers.init_subscription_binding(
            session,
            user,
            SubscriptionBindingInitRequest(
                tier_name="Advanced",
                email="user@example.com",
                method_type="card",
            ),
        )
        assert binding.status == "pending"
        assert binding.payment_url == "https://bind.example.test/card"

        assert (await session.exec(select(Payment))).all() == []
        assert (await session.exec(select(UserSubscription))).all() == []

        binding_row = (await session.exec(select(PaymentBindingSession))).one()
        assert binding_row.request_key == "BIND-CARD-1"
        assert binding_row.status == "pending"

        binding_status = await payment_helpers.get_subscription_binding_status(
            session,
            user,
            binding_row.id,
        )
        assert binding_status.status == "active"
        assert binding_status.payment_method_id is not None

        method = (await session.exec(select(PaymentMethod))).one()
        assert method.is_default is True
        assert method.status == "active"
        assert method.rebill_id == "REBILL-123"

        charge = await payment_helpers.charge_bound_subscription(
            session,
            user,
            BoundSubscriptionChargeRequest(
                tier_name="Advanced",
                email="user@example.com",
                binding_id=binding.binding_id,
            ),
        )
        assert charge.subscription_status == "pending_confirmation"

        payments = (await session.exec(select(Payment))).all()
        assert len(payments) == 1
        assert payments[0].flow_kind == "binding_activation"
        assert payments[0].payment_method_id == method.id
        assert (await session.exec(select(UserSubscription))).all() == []

    await engine.dispose()


@pytest.mark.asyncio
async def test_card_binding_status_returns_failed_instead_of_500_on_provider_error(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)
    monkeypatch.setattr(payment_helpers, "tbank_service", _FailingCardStatusTbankService(), raising=True)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=791000105)
        tier = SubscriptionTier(
            name="Advanced",
            name_ru="Advanced",
            description="",
            description_ru="",
            price_cents=100,
            is_active=True,
            is_public=True,
            is_recurring=True,
        )
        session.add(user)
        session.add(tier)
        await session.commit()
        await session.refresh(user)

        binding = await payment_helpers.init_subscription_binding(
            session,
            user,
            SubscriptionBindingInitRequest(
                tier_name="Advanced",
                email="user@example.com",
                method_type="card",
            ),
        )
        binding_row = (await session.exec(select(PaymentBindingSession))).one()

        status = await payment_helpers.get_subscription_binding_status(session, user, binding_row.id)
        assert status.binding_id == binding.binding_id
        assert status.status == "failed"
        assert status.error_code == "binding_failed"
        assert status.error_message is not None
        assert "Internal error" in status.error_message

        await session.refresh(binding_row)
        assert binding_row.status == "failed"

    await engine.dispose()


@pytest.mark.asyncio
async def test_sbp_binding_status_persists_active_saved_method(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)
    monkeypatch.setattr(payment_helpers, "tbank_service", _FakeTbankService(), raising=True)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=791000104)
        tier = SubscriptionTier(
            name="Advanced",
            name_ru="Advanced",
            description="",
            description_ru="",
            price_cents=100,
            is_active=True,
            is_public=True,
            is_recurring=True,
        )
        session.add(user)
        session.add(tier)
        await session.commit()
        await session.refresh(user)

        binding = await payment_helpers.init_subscription_binding(
            session,
            user,
            SubscriptionBindingInitRequest(
                tier_name="Advanced",
                email="user@example.com",
                method_type="sbp",
            ),
        )
        assert binding.qr_payload == "000201010212..."

        binding_row = (await session.exec(select(PaymentBindingSession))).one()
        status = await payment_helpers.get_subscription_binding_status(session, user, binding_row.id)
        assert status.status == "active"

        method = (await session.exec(select(PaymentMethod))).one()
        assert method.type == "sbp"
        assert method.account_token == "ACCOUNT-TOKEN-1"
        assert method.phone == "+79990001122"
        assert method.is_default is True

    await engine.dispose()


@pytest.mark.asyncio
async def test_sbp_bound_charge_uses_qr_recurrent_init(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)
    fake_tbank = _FakeTbankService()
    monkeypatch.setattr(payment_helpers, "tbank_service", fake_tbank, raising=True)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=791000109)
        tier = SubscriptionTier(
            name="Advanced",
            name_ru="Advanced",
            description="",
            description_ru="",
            price_cents=100,
            is_active=True,
            is_public=True,
            is_recurring=True,
        )
        session.add(user)
        session.add(tier)
        await session.commit()
        await session.refresh(user)

        binding = await payment_helpers.init_subscription_binding(
            session,
            user,
            SubscriptionBindingInitRequest(
                tier_name="Advanced",
                email="user@example.com",
                method_type="sbp",
            ),
        )

        await payment_helpers.charge_bound_subscription(
            session,
            user,
            BoundSubscriptionChargeRequest(
                tier_name="Advanced",
                email="user@example.com",
                binding_id=binding.binding_id,
            ),
        )

        assert fake_tbank.last_init_kwargs is not None
        assert fake_tbank.last_init_kwargs["recurrent"] is True
        assert fake_tbank.last_init_kwargs["data"] == {"QR": "true"}
        assert fake_tbank.last_init_kwargs["operation_initiator_type"] is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_process_due_subscription_renewals_enters_grace_then_expires(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)
    monkeypatch.setattr(payment_helpers, "tbank_service", _FakeTbankService(), raising=True)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=791000102)
        tier = SubscriptionTier(
            name="Advanced",
            name_ru="Advanced",
            description="",
            description_ru="",
            price_cents=100,
            is_active=True,
            is_public=True,
            is_recurring=True,
        )
        session.add(user)
        session.add(tier)
        await session.commit()
        await session.refresh(user)
        await session.refresh(tier)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        sub = UserSubscription(
            user_id=user.id,
            tier_id=tier.id,
            status=SubscriptionStatus.active,
            started_at=now - timedelta(days=31),
            expires_at=now - timedelta(minutes=5),
            auto_renew_enabled=True,
        )
        session.add(sub)
        await session.commit()
        await session.refresh(sub)

        first = await payment_helpers.process_due_subscription_renewals(session)
        assert first["processed"] == 1
        assert first["expired"] == 0

        await session.refresh(sub)
        assert sub.last_renewal_failure_reason == "missing_method"
        assert sub.renewal_grace_until is not None

        sub.renewal_grace_until = now - timedelta(minutes=1)
        session.add(sub)
        await session.commit()

        second = await payment_helpers.process_due_subscription_renewals(session)
        assert second["expired"] == 1

        await session.refresh(sub)
        assert sub.status == SubscriptionStatus.expired

    await engine.dispose()


@pytest.mark.asyncio
async def test_retry_subscription_renewal_uses_selected_active_method(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)
    monkeypatch.setattr(payment_helpers, "tbank_service", _FakeTbankService(), raising=True)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=791000103)
        tier = SubscriptionTier(
            name="Advanced",
            name_ru="Advanced",
            description="",
            description_ru="",
            price_cents=100,
            is_active=True,
            is_public=True,
            is_recurring=True,
        )
        session.add(user)
        session.add(tier)
        await session.commit()
        await session.refresh(user)
        await session.refresh(tier)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        sub = UserSubscription(
            user_id=user.id,
            tier_id=tier.id,
            status=SubscriptionStatus.active,
            started_at=now - timedelta(days=31),
            expires_at=now - timedelta(minutes=5),
            auto_renew_enabled=True,
            renewal_grace_until=now + timedelta(hours=12),
            last_renewal_failure_reason="missing_method",
        )
        method = PaymentMethod(
            user_id=user.id,
            rebill_id="REBILL-RETRY-1",
            type="card",
            card_type="Visa",
            pan="**** 1111",
            exp_date="1230",
            status="active",
            is_default=False,
            bound_at=now - timedelta(days=5),
        )
        session.add(sub)
        session.add(method)
        await session.commit()
        await session.refresh(method)
        await session.refresh(sub)

        result = await payment_helpers.retry_subscription_renewal(
            session,
            user,
            payment_method_id=method.id,
        )
        assert result.subscription_status == "pending_confirmation"

        payment = (await session.exec(select(Payment))).one()
        assert payment.flow_kind == "renewal"
        assert payment.payment_method_id == method.id

        await session.refresh(sub)
        await session.refresh(method)
        assert sub.last_renewal_failure_reason is None
        assert method.last_charge_status == "processing"

    await engine.dispose()


@pytest.mark.asyncio
async def test_current_subscription_refund_status_reports_refundable_window(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)
    monkeypatch.setattr(payment_helpers, "tbank_service", _FakeTbankService(), raising=True)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        user = AppUser(telegram_id=791000106)
        tier = SubscriptionTier(
            name="Advanced",
            name_ru="Advanced",
            description="",
            description_ru="",
            price_cents=100,
            is_active=True,
            is_public=True,
            is_recurring=True,
        )
        session.add(user)
        session.add(tier)
        await session.commit()
        await session.refresh(user)
        await session.refresh(tier)

        sub = UserSubscription(
            user_id=user.id,
            tier_id=tier.id,
            status=SubscriptionStatus.active,
            started_at=now - timedelta(hours=2),
            expires_at=now + timedelta(days=29),
            auto_renew_enabled=True,
        )
        payment = Payment(
            user_id=user.id,
            tier_name=tier.name,
            amount=10000,
            tbank_status="CONFIRMED",
            product_type=PaymentProductType.subscription,
            flow_kind="binding_activation",
            tbank_payment_id="TBANK-REFUND-1",
            created_at=now - timedelta(hours=2),
            updated_at=now - timedelta(hours=2),
        )
        session.add(sub)
        session.add(payment)
        await session.commit()

        result = await payment_helpers.get_current_subscription_refund_status(session, user)
        assert result.refundable is True
        assert result.reason is None
        assert result.payment_id == str(payment.id)
        assert result.window_hours == 24
        assert result.refund_deadline_at is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_current_subscription_refund_status_uses_confirmed_payment_when_subscription_starts_after_payment_creation(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)
    monkeypatch.setattr(payment_helpers, "tbank_service", _FakeTbankService(), raising=True)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        user = AppUser(telegram_id=791000109)
        tier = SubscriptionTier(
            name="Advanced",
            name_ru="Advanced",
            description="",
            description_ru="",
            price_cents=100,
            is_active=True,
            is_public=True,
            is_recurring=True,
        )
        session.add(user)
        session.add(tier)
        await session.commit()
        await session.refresh(user)
        await session.refresh(tier)

        sub = UserSubscription(
            user_id=user.id,
            tier_id=tier.id,
            status=SubscriptionStatus.active,
            started_at=now,
            expires_at=now + timedelta(days=29),
            auto_renew_enabled=True,
        )
        payment = Payment(
            user_id=user.id,
            tier_name=tier.name,
            amount=10000,
            tbank_status="CONFIRMED",
            product_type=PaymentProductType.subscription,
            flow_kind="binding_activation",
            tbank_payment_id="TBANK-REFUND-1B",
            created_at=now - timedelta(minutes=3),
            updated_at=now - timedelta(milliseconds=12),
        )
        session.add(sub)
        session.add(payment)
        await session.commit()

        result = await payment_helpers.get_current_subscription_refund_status(session, user)
        assert result.refundable is True
        assert result.reason is None
        assert result.payment_id == str(payment.id)

    await engine.dispose()


@pytest.mark.asyncio
async def test_current_subscription_refund_status_expires_after_24_hours(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)
    monkeypatch.setattr(payment_helpers, "tbank_service", _FakeTbankService(), raising=True)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        user = AppUser(telegram_id=791000107)
        tier = SubscriptionTier(
            name="Advanced",
            name_ru="Advanced",
            description="",
            description_ru="",
            price_cents=100,
            is_active=True,
            is_public=True,
            is_recurring=True,
        )
        session.add(user)
        session.add(tier)
        await session.commit()
        await session.refresh(user)
        await session.refresh(tier)

        sub = UserSubscription(
            user_id=user.id,
            tier_id=tier.id,
            status=SubscriptionStatus.active,
            started_at=now - timedelta(days=2),
            expires_at=now + timedelta(days=28),
            auto_renew_enabled=True,
        )
        payment = Payment(
            user_id=user.id,
            tier_name=tier.name,
            amount=10000,
            tbank_status="CONFIRMED",
            product_type=PaymentProductType.subscription,
            flow_kind="binding_activation",
            tbank_payment_id="TBANK-REFUND-2",
            created_at=now - timedelta(hours=26),
            updated_at=now - timedelta(hours=26),
        )
        session.add(sub)
        session.add(payment)
        await session.commit()

        result = await payment_helpers.get_current_subscription_refund_status(session, user)
        assert result.refundable is False
        assert result.reason == "window_expired"

    await engine.dispose()


@pytest.mark.asyncio
async def test_refund_current_subscription_marks_payment_refunded_and_cancels_access(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)
    monkeypatch.setattr(payment_helpers, "tbank_service", _FakeTbankService(), raising=True)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        user = AppUser(telegram_id=791000108)
        tier = SubscriptionTier(
            name="Advanced",
            name_ru="Advanced",
            description="",
            description_ru="",
            price_cents=100,
            is_active=True,
            is_public=True,
            is_recurring=True,
        )
        session.add(user)
        session.add(tier)
        await session.commit()
        await session.refresh(user)
        await session.refresh(tier)

        sub = UserSubscription(
            user_id=user.id,
            tier_id=tier.id,
            status=SubscriptionStatus.active,
            started_at=now - timedelta(hours=3),
            expires_at=now + timedelta(days=30),
            auto_renew_enabled=True,
        )
        payment = Payment(
            user_id=user.id,
            tier_name=tier.name,
            amount=10000,
            tbank_status="CONFIRMED",
            product_type=PaymentProductType.subscription,
            flow_kind="binding_activation",
            tbank_payment_id="TBANK-REFUND-3",
            created_at=now - timedelta(hours=3),
            updated_at=now - timedelta(hours=3),
        )
        session.add(sub)
        session.add(payment)
        await session.commit()

        result = await payment_helpers.refund_current_subscription(session, user)
        assert result.status == "REFUNDED"
        assert result.payment_id == str(payment.id)

        await session.refresh(payment)
        await session.refresh(sub)
        assert payment.tbank_status == "REFUNDED"
        assert sub.status == SubscriptionStatus.cancelled
        assert sub.auto_renew_enabled is False

    await engine.dispose()


@pytest.mark.asyncio
async def test_get_user_agreement_returns_public_offer_text():
    result = await payment_helpers.get_user_agreement()
    assert result.document_key == "public_offer"
    assert result.lang == "ru"
    assert "регулярные списания" in result.text
    assert "support@lightny.pro" in result.text
