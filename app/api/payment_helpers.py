import uuid
from datetime import datetime, timezone
import re

from dateutil.relativedelta import relativedelta
from fastapi import BackgroundTasks, HTTPException, Response
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.metrics import track_event, track_value
from app.db.models import Payment, PaymentMethod, PaymentProductType, AppUser, PaymentMethodType
from app.db.subscription_tiers import (
    GeneralDiscount,
    SubscriptionStatus,
    SubscriptionTier,
    UsagePack,
    UsagePackSource,
    UserSubscription,
    UserUsagePack,
)
from app.schemas.subscriptions import (
    InitPaymentRequest,
    InitUsagePackPaymentRequest,
    PaymentInitResponse,
    PaymentStatusResponse,
    MockUsagePackPurchaseRequest,
)
from app.services.banking.tbank import tbank_service

logger = settings.custom_logger


def _tier_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "tier"


async def _first_purchase_available(session: AsyncSession, user_id: uuid.UUID) -> bool:
    existing_paid_subscription = await session.exec(
        select(Payment.id).where(
            Payment.user_id == user_id,
            Payment.product_type == PaymentProductType.subscription,
            Payment.amount > 0,
            Payment.tbank_status == "CONFIRMED",
        )
    )
    return existing_paid_subscription.first() is None


async def _eligible_general_discounts_for_tier(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    tier: SubscriptionTier,
) -> list[GeneralDiscount]:
    now = datetime.utcnow()
    rows = (await session.exec(
        select(GeneralDiscount).where(
            GeneralDiscount.is_active == True,  # noqa: E712
            (GeneralDiscount.starts_at.is_(None)) | (GeneralDiscount.starts_at <= now),
            (GeneralDiscount.expires_at.is_(None)) | (GeneralDiscount.expires_at > now),
        )
    )).all()

    tier_slug = _tier_slug(tier.name)
    first_purchase_available = await _first_purchase_available(session, user_id)
    eligible: list[GeneralDiscount] = []
    for gd in rows:
        applies_to = gd.applies_to_tiers or ["all"]
        applies_norm = {str(item).strip().lower() for item in applies_to}
        if "all" not in applies_norm and tier_slug.lower() not in applies_norm:
            continue

        conditions = gd.conditions or {}
        if conditions.get("no_prior_paid_sub") and not first_purchase_available:
            continue

        eligible.append(gd)
    return eligible


def _apply_discount_stack(base_amount_cents: int, discounts: list[GeneralDiscount]) -> int:
    amount = float(base_amount_cents)
    if not discounts:
        return int(round(amount))

    any_non_stackable = any(not d.stackable for d in discounts)
    if any_non_stackable:
        best = max(discounts, key=lambda d: int(d.percent_off or 0))
        pct = max(0, min(100, int(best.percent_off or 0)))
        return int(round(amount * (1 - pct / 100)))

    for d in discounts:
        pct = max(0, min(100, int(d.percent_off or 0)))
        amount *= (1 - pct / 100)
    return int(round(amount))


async def init_subscription_payment(
    session: AsyncSession,
    user,
    payload: InitPaymentRequest,
) -> PaymentInitResponse:
    tier_name = payload.tier_name
    email = payload.email

    result = await session.exec(select(SubscriptionTier).where(SubscriptionTier.name == tier_name))
    tier = result.first()
    if not tier:
        raise HTTPException(status_code=404, detail="Tier not found")

    base_amount_cents = int(tier.price_cents * 100)
    requested_codes = {c.strip().lower() for c in (payload.discount_codes or []) if str(c).strip()}
    eligible_discounts = await _eligible_general_discounts_for_tier(
        session,
        user_id=user.id,
        tier=tier,
    )
    if requested_codes:
        selected_discounts = [
            d for d in eligible_discounts
            if d.code and d.code.strip().lower() in requested_codes
        ]
    else:
        selected_discounts = eligible_discounts

    amount_cents = _apply_discount_stack(base_amount_cents, selected_discounts)
    applied_codes = [d.code for d in selected_discounts if d.code]
    discount_amount_cents = max(0, base_amount_cents - amount_cents)

    payment = Payment(
        user_id=user.id,
        tier_name=tier.name,
        amount=amount_cents,
        tbank_status="NEW",
        product_type=PaymentProductType.subscription,
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)

    receipt_data = None
    if email:
        receipt_data = {
            "Taxation": settings.TBANK_TAXATION,
            "Email": email,
            "Items": [
                {
                    "Name": f"Subscription: {tier.name}",
                    "Price": amount_cents,
                    "Quantity": 1,
                    "Amount": amount_cents,
                    "Tax": "none",
                    "PaymentMethod": "full_prepayment",
                    "PaymentObject": "service",
                }
            ],
        }

    try:
        # TODO: Need to figure out how to have the QR code thing
        # Pass QR: true to enable SBP recurring flow if selected by user
        extra_data = {"QR": "true"}

        payment_url, tbank_id = await tbank_service.init_payment(
            order_id=str(payment.id),
            amount_cents=amount_cents,
            description=f"Subscription: {tier.name}",
            user_id=str(user.id),
            recurrent="Y",
            receipt=receipt_data,
        )

        if applied_codes:
            logger.info(
                "Applied subscription discounts user_id=%s tier=%s codes=%s discount_cents=%s",
                user.id,
                tier.name,
                ",".join(applied_codes),
                discount_amount_cents,
            )

        payment.tbank_payment_id = tbank_id
        session.add(payment)
        await session.commit()

        return PaymentInitResponse(
            payment_url=payment_url,
            payment_id=str(payment.id),
        )
    except Exception as exc:
        payment.tbank_status = "ERROR"
        session.add(payment)
        await session.commit()
        logger.exception(
            "TBank init subscription failed: payment_id=%s user_id=%s tier=%s",
            payment.id,
            user.id,
            tier_name,
        )
        detail = str(exc).strip() or exc.__class__.__name__
        raise HTTPException(status_code=500, detail=detail)


async def init_usage_pack_payment(
    session: AsyncSession,
    user,
    payload: InitUsagePackPaymentRequest,
) -> PaymentInitResponse:
    try:
        pack_id = uuid.UUID(payload.pack_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid pack_id")

    pack = (await session.exec(
        select(UsagePack).where(
            UsagePack.id == pack_id,
            UsagePack.is_active == True,
            UsagePack.is_public == True,
        )
    )).first()
    if not pack:
        raise HTTPException(status_code=404, detail="Usage pack not found")

    amount_cents = int(pack.price_cents * 100)

    payment = Payment(
        user_id=user.id,
        tier_name=pack.name,
        amount=amount_cents,
        tbank_status="NEW",
        product_type=PaymentProductType.usage_pack,
        pack_id=pack.id,
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)

    receipt_data = None
    if payload.email:
        receipt_data = {
            "Taxation": settings.TBANK_TAXATION,
            "Email": payload.email,
            "Items": [
                {
                    "Name": f"Usage pack: {pack.name}",
                    "Price": amount_cents,
                    "Quantity": 1,
                    "Amount": amount_cents,
                    "Tax": "none",
                    "PaymentMethod": "full_prepayment",
                    "PaymentObject": "service",
                }
            ],
        }

    try:
        # Pass QR: true to enable SBP options (though usage packs might not be recurring,
        # passing it doesn't hurt if Recurrent is False, but usually we don't save token for one-off)
        # However, InitPaymentRequest for usage pack has recurrent=False in original code.
        # If we want to allow saving card for future usage packs, we'd need recurrent=True.
        # Original code had recurrent=False. I'll stick to False but pass data just in case.
        extra_data = {"QR": "true"}

        payment_url, tbank_id = await tbank_service.init_payment(
            order_id=str(payment.id),
            amount_cents=amount_cents,
            description=f"Usage pack: {pack.name}",
            user_id=str(user.id),
            recurrent=False,
            receipt=receipt_data,
            data=extra_data,
        )

        payment.tbank_payment_id = tbank_id
        session.add(payment)
        await session.commit()

        return PaymentInitResponse(
            payment_url=payment_url,
            payment_id=str(payment.id),
        )
    except Exception as exc:
        payment.tbank_status = "ERROR"
        session.add(payment)
        await session.commit()
        logger.exception(
            "TBank init usage-pack failed: payment_id=%s user_id=%s pack_id=%s",
            payment.id,
            user.id,
            payload.pack_id,
        )
        detail = str(exc).strip() or exc.__class__.__name__
        raise HTTPException(status_code=500, detail=detail)


async def get_payment_status(
    session: AsyncSession,
    payment_id: uuid.UUID,
    user: AppUser,
) -> PaymentStatusResponse:
    payment = await session.get(Payment, payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment.user_id != user.id:
        raise HTTPException(status_code=404, detail="Payment not found")

    return PaymentStatusResponse(
        id=str(payment.id),
        status=payment.tbank_status,
        is_confirmed=payment.tbank_status == "CONFIRMED",
        tier_name=payment.tier_name,
        product_type=payment.product_type.value,
        product_name=payment.tier_name,
        pack_id=str(payment.pack_id) if payment.pack_id else None,
    )


async def handle_tbank_webhook(
    session: AsyncSession,
    background_tasks: BackgroundTasks,
    data: dict,
) -> Response:
    if not tbank_service.verify_notification(data):
        logger.error("Webhook signature verification failed. Data: %s", data)
        return Response(content="OK", media_type="text/plain")

    order_id = data.get("OrderId")
    new_status = data.get("Status")
    success = data.get("Success", False)

    try:
        payment_uuid = uuid.UUID(order_id)
        query = select(Payment).where(Payment.id == payment_uuid).with_for_update()
        result = await session.exec(query)
        payment = result.first()
    except ValueError:
        return Response(content="OK", media_type="text/plain")

    if not payment:
        return Response(content="OK", media_type="text/plain")

    final_states = {"CONFIRMED", "CANCELED", "REJECTED", "REFUNDED"}
    if payment.tbank_status in final_states:
        return Response(content="OK", media_type="text/plain")

    payment.tbank_status = new_status
    session.add(payment)

    if new_status == "CONFIRMED" and success:
        try:
            await save_payment_method(session, payment.user_id, data)
        except Exception as exc:
            logger.error("Failed to save payment method: %s", exc)

        if payment.product_type == PaymentProductType.usage_pack:
            settings.custom_logger.info("Payment %s confirmed! Adding usage pack...", payment_uuid)
            await activate_usage_pack(session, payment)
            settings.custom_logger.info("Usage pack added!")

            background_tasks.add_task(
                track_event,
                "usage_pack_purchased",
                str(payment.user_id),
                {"pack": payment.tier_name},
            )
        else:
            settings.custom_logger.info("Payment %s confirmed! Adding subscription...", payment_uuid)
            await activate_subscription(session, payment)
            settings.custom_logger.info("Subscription added!")

            background_tasks.add_task(
                track_event,
                "subscription_purchased",
                str(payment.user_id),
                {"tier": payment.tier_name},
            )

        background_tasks.add_task(
            track_value,
            "revenue",
            float(payment.amount),
            str(payment.user_id),
            {"tier": payment.tier_name},
            unit="rub",
        )

    await session.commit()
    return Response(content="OK", media_type="text/plain")


async def activate_subscription(session: AsyncSession, payment: Payment) -> None:
    tier = (await session.exec(
        select(SubscriptionTier).where(SubscriptionTier.name == payment.tier_name)
    )).first()
    if not tier:
        logger.info("Tier %s not found during activation", payment.tier_name)
        return

    query = select(UserSubscription).where(
        UserSubscription.user_id == payment.user_id,
        UserSubscription.status == SubscriptionStatus.active,
    )
    active_subs = (await session.exec(query)).all()

    for sub in active_subs:
        sub.status = SubscriptionStatus.cancelled
        session.add(sub)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    expires_at = now + relativedelta(months=1)

    new_sub = UserSubscription(
        user_id=payment.user_id,
        tier_id=tier.id,
        status=SubscriptionStatus.active,
        started_at=now,
        expires_at=expires_at,
    )
    session.add(new_sub)


async def activate_usage_pack(session: AsyncSession, payment: Payment) -> None:
    if not payment.pack_id:
        return

    pack = await session.get(UsagePack, payment.pack_id)
    if not pack or not pack.is_active:
        return

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    pack_purchase = UserUsagePack(
        user_id=payment.user_id,
        pack_id=pack.id,
        source=UsagePackSource.paid,
        purchased_at=now,
        expires_at=None,
        payment_id=payment.id,
    )
    session.add(pack_purchase)


async def save_payment_method(session: AsyncSession, user_id: uuid.UUID, webhook_data: dict = None) -> None:
    """
    Saves payment method from webhook data (preferred) or fetches card list (fallback).
    Supports both Card (RebillId) and SBP (AccountToken).
    """
    # 1. Try to find AccountToken (SBP)
    if webhook_data and "AccountToken" in webhook_data:
        account_token = webhook_data["AccountToken"]
        phone = webhook_data.get("Phone")

        # Check if exists
        existing = await session.exec(select(PaymentMethod).where(PaymentMethod.account_token == account_token))
        if existing.first():
            return

        method = PaymentMethod(
            user_id=user_id,
            account_token=account_token,
            type=PaymentMethodType.sbp.value,
            phone=phone,
            is_default=True
        )
        session.add(method)
        logger.info(f"Saved SBP payment method for user {user_id}")
        return

    # 2. Try to find RebillId (Card) in webhook
    if webhook_data and "RebillId" in webhook_data:
        rebill_id = str(webhook_data["RebillId"])
        pan = webhook_data.get("Pan", "****")
        # Ensure we don't save duplicates
        existing = await session.exec(select(PaymentMethod).where(PaymentMethod.rebill_id == rebill_id))
        if existing.first():
            return

        method = PaymentMethod(
            user_id=user_id,
            rebill_id=rebill_id,
            type=PaymentMethodType.card.value,
            pan=pan,
            card_type="Card", # Usually not provided in webhook detail, can be updated later if needed
            exp_date=webhook_data.get("ExpDate", ""),
            is_default=True,
        )
        session.add(method)
        logger.info(f"Saved Card payment method for user {user_id} from webhook")
        return

    # 3. Fallback: GetCardList (Legacy behavior for Cards)
    cards = await tbank_service.get_card_list(str(user_id))
    if not cards:
        return

    latest_card = cards[-1]
    rebill_id = str(latest_card.get("RebillId"))
    pan = latest_card.get("Pan", "****")
    card_type = latest_card.get("CardType", "Unknown")
    exp = latest_card.get("ExpDate", "")

    existing = await session.exec(select(PaymentMethod).where(PaymentMethod.rebill_id == rebill_id))
    if existing.first():
        return

    method = PaymentMethod(
        user_id=user_id,
        rebill_id=rebill_id,
        pan=pan,
        card_type=str(card_type),
        exp_date=exp,
        is_default=True,
        type=PaymentMethodType.card.value
    )
    session.add(method)
    logger.info(f"Saved Card payment method for user {user_id} from GetCardList")


async def mock_usage_pack_purchase(
    session: AsyncSession,
    background_tasks: BackgroundTasks,
    payload: MockUsagePackPurchaseRequest,
) -> Response:
    """
    Mocks the purchase of a usage pack.
    1. Creates a Payment record (NEW).
    2. Simulates a webhook callback (CONFIRMED).
    """
    try:
        user_id = uuid.UUID(payload.user_id)
        pack_id = uuid.UUID(payload.pack_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format")

    user = await session.get(AppUser, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    pack = await session.get(UsagePack, pack_id)
    if not pack:
        raise HTTPException(status_code=404, detail="Usage pack not found")

    amount_cents = int(pack.price_cents * 100)

    # 1. Create Payment record
    payment = Payment(
        user_id=user.id,
        tier_name=pack.name,
        amount=amount_cents,
        tbank_status="NEW",
        product_type=PaymentProductType.usage_pack,
        pack_id=pack.id,
        tbank_payment_id=f"MOCK-{uuid.uuid4()}",
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)

    # 2. Simulate Webhook (CONFIRMED)
    # We can reuse handle_tbank_webhook logic, but we need to bypass signature verification.
    # Or we can just directly update the payment and call activate_usage_pack.
    # Let's do the latter to be more direct and avoid mocking tbank_service.verify_notification.

    payment.tbank_status = "CONFIRMED"
    session.add(payment)

    settings.custom_logger.info("Mock Payment %s confirmed! Adding usage pack...", payment.id)
    await activate_usage_pack(session, payment)
    settings.custom_logger.info("Usage pack added!")

    background_tasks.add_task(
        track_event,
        "usage_pack_purchased_mock",
        str(payment.user_id),
        {"pack": payment.tier_name},
    )

    await session.commit()

    return Response(content=f"Mock purchase successful. Payment ID: {payment.id}", media_type="text/plain")
