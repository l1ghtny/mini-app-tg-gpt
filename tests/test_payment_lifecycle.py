import uuid
from datetime import timedelta
from fastapi import BackgroundTasks, HTTPException
import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from app.db.database import engine
from app.db.models import AppUser, Payment
from app.db.subscription_tiers import (
    SubscriptionTier,
    UserSubscription,
    SubscriptionStatus,
)
from app.api import payment_helpers, user_subscription_helpers


async def setup_payment(name="Paid plan"):
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=int(uuid.uuid4().int % 10**12))
        tier = SubscriptionTier(
            name=name,
            name_ru=name,
            description="Test",
            description_ru="Test",
            price_cents=100,
            is_active=True,
            is_public=True,
            is_recurring=True,
        )
        session.add(user)
        session.add(tier)
        await session.flush()
        payment = Payment(
            user_id=user.id,
            tier_name=tier.name,
            amount=10000,
            tbank_status="NEW",
            tbank_payment_id="provider-1",
        )
        session.add(payment)
        await session.commit()
        return user, tier, payment


def notification(payment, status="CONFIRMED", **changes):
    return {
        "OrderId": str(payment.id),
        "PaymentId": payment.tbank_payment_id,
        "Amount": payment.amount,
        "Status": status,
        "Success": True,
        **changes,
    }


@pytest.fixture(autouse=True)
def trusted_synthetic_bank(monkeypatch):
    monkeypatch.setattr(
        payment_helpers.tbank_service, "verify_notification", lambda data: True
    )

    async def no_sample(*args, **kwargs):
        return None

    monkeypatch.setattr(payment_helpers, "get_recent_premium_sample_kind", no_sample)


@pytest.mark.asyncio
async def test_confirmation_refund_duplicate_and_late_callbacks_do_not_repeat_effects():
    user, tier, payment = await setup_payment()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        tasks = BackgroundTasks()
        await payment_helpers.handle_tbank_webhook(
            session, tasks, notification(payment)
        )
        row = await session.get(Payment, payment.id)
        subscription = await session.get(UserSubscription, row.subscription_id)
        expiry = subscription.expires_at
        assert (
            row.confirmation_applied
            and row.confirmed_at
            and subscription.status == SubscriptionStatus.active
        )
        assert any(
            task.func is payment_helpers.track_value and task.args[1] == 100.0
            for task in tasks.tasks
        )
        await payment_helpers.handle_tbank_webhook(
            session, BackgroundTasks(), notification(payment)
        )
        assert subscription.expires_at == expiry
        await payment_helpers.handle_tbank_webhook(
            session, BackgroundTasks(), notification(payment, "REFUNDED")
        )
        await session.refresh(row)
        await session.refresh(subscription)
        assert row.tbank_status == "REFUNDED" and row.refund_applied and row.refunded_at
        assert subscription.status == SubscriptionStatus.cancelled
        refund_expiry = subscription.expires_at
        for status in ("REFUNDED", "CONFIRMED", "REJECTED", "AUTHORIZED"):
            await payment_helpers.handle_tbank_webhook(
                session, BackgroundTasks(), notification(payment, status)
            )
        await session.refresh(subscription)
        await session.refresh(row)
        assert (
            subscription.expires_at == refund_expiry and row.tbank_status == "REFUNDED"
        )
        assert not await payment_helpers._first_purchase_available(session, user.id)
        assert not await user_subscription_helpers._first_purchase_available(
            session, user.id
        )


@pytest.mark.asyncio
async def test_refund_of_old_payment_does_not_revoke_new_subscription():
    user, tier, payment = await setup_payment()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await payment_helpers.handle_tbank_webhook(
            session, BackgroundTasks(), notification(payment)
        )
        old = (await session.exec(select(UserSubscription))).one()
        newer = Payment(
            user_id=user.id,
            tier_name=tier.name,
            amount=10000,
            tbank_payment_id="provider-2",
        )
        session.add(newer)
        await session.commit()
        await payment_helpers.handle_tbank_webhook(
            session, BackgroundTasks(), notification(newer)
        )
        row = await session.get(Payment, newer.id)
        active = await session.get(UserSubscription, row.subscription_id)
        expiry = active.expires_at
        await payment_helpers.handle_tbank_webhook(
            session, BackgroundTasks(), notification(payment, "REFUNDED")
        )
        await session.refresh(active)
        assert (
            active.id != old.id
            and active.status == SubscriptionStatus.active
            and active.expires_at == expiry
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [{"PaymentId": "different"}, {"Amount": 1}, {"Success": False}, {"Amount": True}],
)
async def test_mismatched_or_unsuccessful_confirmation_cannot_grant_access(changes):
    _, _, payment = await setup_payment()
    async with AsyncSession(engine) as session:
        await payment_helpers.handle_tbank_webhook(
            session, BackgroundTasks(), notification(payment, **changes)
        )
        assert not (await session.exec(select(UserSubscription))).all()
        assert (await session.get(Payment, payment.id)).tbank_status == "NEW"


@pytest.mark.asyncio
async def test_legacy_refund_does_not_guess_a_subscription_to_cancel():
    user, tier, payment = await setup_payment()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        row = await session.get(Payment, payment.id)
        row.tbank_status = "CONFIRMED"
        subscription = UserSubscription(
            user_id=user.id,
            tier_id=tier.id,
            expires_at=payment_helpers._utcnow_naive() + timedelta(days=30),
        )
        session.add(subscription)
        await session.commit()
        await payment_helpers.handle_tbank_webhook(
            session, BackgroundTasks(), notification(payment, "REFUNDED")
        )
        await session.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active
        assert row.tbank_status == "REFUNDED" and not row.refund_applied


@pytest.mark.asyncio
async def test_pending_refund_is_not_reported_as_completed_and_later_callback_is_idempotent(
    monkeypatch,
):
    user, _, payment = await setup_payment()

    async def pending(payment_id, amount=None):
        return {"Success": True, "PaymentId": payment_id, "Status": "REFUNDING"}

    monkeypatch.setattr(payment_helpers.tbank_service, "cancel_payment", pending)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await payment_helpers.handle_tbank_webhook(
            session, BackgroundTasks(), notification(payment)
        )
        result = await payment_helpers.refund_current_subscription(session, user)
        assert result.status == "REFUNDING" and result.refunded_at is None
        row = await session.get(Payment, payment.id)
        subscription = await session.get(UserSubscription, row.subscription_id)
        expiry = subscription.expires_at
        assert (
            row.refund_requested_at
            and row.refund_applied
            and not subscription.auto_renew_enabled
        )
        await payment_helpers.handle_tbank_webhook(
            session, BackgroundTasks(), notification(payment, "REFUNDED")
        )
        await session.refresh(row)
        await session.refresh(subscription)
        assert row.refunded_at and subscription.expires_at == expiry


@pytest.mark.asyncio
async def test_wrong_cancel_response_cannot_claim_a_refund(monkeypatch):
    user, _, payment = await setup_payment()

    async def wrong(payment_id, amount=None):
        return {"Success": True, "PaymentId": "different", "Status": "REFUNDED"}

    monkeypatch.setattr(payment_helpers.tbank_service, "cancel_payment", wrong)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await payment_helpers.handle_tbank_webhook(
            session, BackgroundTasks(), notification(payment)
        )
        with pytest.raises(HTTPException) as error:
            await payment_helpers.refund_current_subscription(session, user)
        assert error.value.status_code == 502
        assert (await session.get(Payment, payment.id)).tbank_status == "CONFIRMED"


@pytest.mark.asyncio
async def test_payment_for_deleted_account_is_recorded_without_restoring_access():
    user, _, payment = await setup_payment()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        row = await session.get(AppUser, user.id)
        row.deleted_at = payment_helpers._utcnow_naive()
        await session.commit()
        await payment_helpers.handle_tbank_webhook(
            session, BackgroundTasks(), notification(payment)
        )
        assert not (await session.exec(select(UserSubscription))).all()
        captured = await session.get(Payment, payment.id)
        assert (
            captured.confirmation_applied
            and captured.renewal_failure_reason == "account_deleted"
        )


@pytest.mark.asyncio
async def test_public_mapping_grants_paid_period_and_preserves_private_access(
    monkeypatch,
):
    from app.services import allowance
    from app.core.config import settings

    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_TRIAL_ENABLED", True)
    user, tier, payment = await setup_payment()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        paid_tier = await session.get(SubscriptionTier, tier.id)
        paid_tier.allowance_plan_key = "start"
        paid_tier.is_recurring = False
        gift_tier = SubscriptionTier(
            name="Smooth tier",
            name_ru="Smooth",
            description="Gift",
            description_ru="Gift",
            price_cents=0,
            is_active=True,
            is_public=False,
        )
        session.add(gift_tier)
        await session.flush()
        gift = UserSubscription(
            user_id=user.id,
            tier_id=gift_tier.id,
            started_at=payment_helpers._utcnow_naive(),
            expires_at=payment_helpers._utcnow_naive() + timedelta(days=7),
        )
        session.add(gift)
        await session.commit()
        await payment_helpers.handle_tbank_webhook(
            session, BackgroundTasks(), notification(payment)
        )
        row = await session.get(Payment, payment.id)
        paid = await session.get(UserSubscription, row.subscription_id)
        assert not paid.auto_renew_enabled and gift.status == SubscriptionStatus.active
        access = await allowance.private_access(session, user.id)
        assert (
            access.tier_name == gift_tier.name and access.expires_at == gift.expires_at
        )
        gift.status = SubscriptionStatus.expired
        await session.commit()
        access = await allowance.private_access(session, user.id)
        assert (
            access.plan == "start"
            and access.started_at == paid.started_at
            and access.expires_at == paid.expires_at
        )


@pytest.mark.asyncio
async def test_shared_account_legacy_checkout_is_closed_before_bank_call(monkeypatch):
    from app.services import allowance
    from app.schemas.subscriptions import SubscriptionBindingInitRequest
    from unittest.mock import AsyncMock

    user, tier, _ = await setup_payment()
    monkeypatch.setattr(allowance, "enabled", lambda _: True)
    bank = AsyncMock()
    monkeypatch.setattr(payment_helpers.tbank_service, "add_card", bank)
    async with AsyncSession(engine) as session:
        with pytest.raises(HTTPException) as error:
            await payment_helpers.init_subscription_binding(
                session,
                user,
                SubscriptionBindingInitRequest(
                    tier_name=tier.name, email="test@example.invalid"
                ),
            )
        assert error.value.status_code == 409
        bank.assert_not_awaited()


@pytest.mark.asyncio
async def test_refund_window_starts_at_confirmation_and_returns_utc_times():
    user, _, payment = await setup_payment()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        row = await session.get(Payment, payment.id)
        row.created_at -= timedelta(days=2)
        await session.commit()
        await payment_helpers.handle_tbank_webhook(
            session,
            BackgroundTasks(),
            notification(payment, Amount=str(payment.amount)),
        )
        status = await payment_helpers.get_current_subscription_refund_status(
            session, user
        )
        assert (
            status.refundable
            and status.purchased_at.endswith("+00:00")
            and status.refund_deadline_at.endswith("+00:00")
        )


@pytest.mark.asyncio
async def test_concurrent_confirmation_callbacks_grant_exactly_once():
    import asyncio

    user, _, payment = await setup_payment()

    async def callback():
        async with AsyncSession(engine) as session:
            await payment_helpers.handle_tbank_webhook(
                session, BackgroundTasks(), notification(payment)
            )

    await asyncio.gather(callback(), callback())
    async with AsyncSession(engine) as session:
        subscriptions = (
            await session.exec(
                select(UserSubscription).where(UserSubscription.user_id == user.id)
            )
        ).all()
        assert len(subscriptions) == 1
        row = await session.get(Payment, payment.id)
        assert row.subscription_id == subscriptions[0].id and row.confirmation_applied


@pytest.mark.asyncio
async def test_renewal_refund_reverses_recorded_interval_only(monkeypatch):
    from datetime import datetime

    user, _, payment = await setup_payment()
    clock = datetime(2026, 1, 31, 12)
    monkeypatch.setattr(payment_helpers, "_utcnow_naive", lambda: clock)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await payment_helpers.handle_tbank_webhook(
            session, BackgroundTasks(), notification(payment)
        )
        first = await session.get(Payment, payment.id)
        subscription = await session.get(UserSubscription, first.subscription_id)
        original_expiry = subscription.expires_at
        renewal = Payment(
            user_id=user.id,
            tier_name=payment.tier_name,
            amount=payment.amount,
            tbank_payment_id="renewal",
            flow_kind="renewal",
            subscription_id=subscription.id,
        )
        session.add(renewal)
        await session.commit()
        await payment_helpers.handle_tbank_webhook(
            session, BackgroundTasks(), notification(renewal)
        )
        await session.refresh(renewal)
        await session.refresh(subscription)
        assert (
            renewal.subscription_period_start == original_expiry
            and renewal.subscription_period_end > original_expiry
        )
        await payment_helpers.handle_tbank_webhook(
            session, BackgroundTasks(), notification(renewal, "REFUNDED")
        )
        await session.refresh(subscription)
        assert (
            subscription.status == SubscriptionStatus.active
            and subscription.expires_at == original_expiry
        )


@pytest.mark.asyncio
async def test_payment_migration_preserves_history_without_guessing_subscription_links():
    from importlib import import_module
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    user, _, payment = await setup_payment(name="Basic")
    migration = import_module("migrations.versions.xw0e1f2a3b71_payment_lifecycle")

    def apply(connection, action):
        with Operations.context(MigrationContext.configure(connection)):
            action()

    async with engine.begin() as conn:
        await conn.execute(
            text("update payment set tbank_status='CONFIRMED' where id=:id"),
            {"id": payment.id},
        )
        await conn.run_sync(lambda c: apply(c, migration.downgrade))
        await conn.run_sync(lambda c: apply(c, migration.upgrade))
        result = (
            await conn.execute(
                text(
                    "select confirmation_applied,subscription_id,confirmed_at from payment where id=:id"
                ),
                {"id": payment.id},
            )
        ).one()
        assert result == (True, None, None)
        assert (
            await conn.execute(
                text(
                    "select allowance_plan_key from subscription_tier where name='Basic'"
                )
            )
        ).scalar_one() == "start"
