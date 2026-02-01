import uuid
from datetime import datetime, timezone

from dateutil.relativedelta import relativedelta
from fastapi import BackgroundTasks, HTTPException, Response
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.metrics import track_event, track_value
from app.db.models import Payment, PaymentMethod, PaymentProductType
from app.db.subscription_tiers import (
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
)
from app.services.banking.tbank import tbank_service

logger = settings.custom_logger


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

    amount_cents = int(tier.price_cents * 100)

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
        payment_url, tbank_id = await tbank_service.init_payment(
            order_id=str(payment.id),
            amount_cents=amount_cents,
            description=f"Subscription: {tier.name}",
            user_id=str(user.id),
            recurrent=True,
            receipt=receipt_data,
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
        raise HTTPException(status_code=500, detail=str(exc))


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
        payment_url, tbank_id = await tbank_service.init_payment(
            order_id=str(payment.id),
            amount_cents=amount_cents,
            description=f"Usage pack: {pack.name}",
            user_id=str(user.id),
            recurrent=False,
            receipt=receipt_data,
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
        raise HTTPException(status_code=500, detail=str(exc))


async def get_payment_status(session: AsyncSession, payment_id: uuid.UUID) -> PaymentStatusResponse:
    payment = await session.get(Payment, payment_id)
    if not payment:
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
        settings.logger.error("Webhook signature verification failed. Data: %s", data)
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
            await save_payment_method(session, payment.user_id)
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


async def save_payment_method(session: AsyncSession, user_id: uuid.UUID) -> None:
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
    )
    session.add(method)
