import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from dateutil.relativedelta import relativedelta
from fastapi import APIRouter, Depends, HTTPException, Body, Request, Response
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.db.database import get_session
from app.api.dependencies import get_current_user
from app.db.models import AppUser, Payment, PaymentMethod
from app.db.subscription_tiers import SubscriptionTier, UserSubscription, SubscriptionStatus
from app.schemas.subscriptions import InitPaymentRequest
from app.services.banking.tbank import tbank_service

payments = APIRouter(tags=["payments"], prefix="/payments/tbank")

logger = settings.custom_logger


@payments.post("/init")
async def init_payment(
        payload: InitPaymentRequest,
        user: AppUser = Depends(get_current_user),
        session: AsyncSession = Depends(get_session)
):
    tier_name = payload.tier_name
    email = payload.email

    # 1. Fetch Tier
    result = await session.exec(select(SubscriptionTier).where(SubscriptionTier.name == tier_name))
    tier = result.first()
    if not tier:
        raise HTTPException(status_code=404, detail="Tier not found")

    amount_cents = int(tier.price_cents * 100)

    # 2. Create Payment Record (Same as before)
    payment = Payment(
        user_id=user.id,
        tier_name=tier.name,
        amount=amount_cents,
        tbank_status="NEW"
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)

    # 3. Construct Receipt (54-FZ)
    receipt_data = None
    if email:  # Only create a receipt if we have an email/phone to send it to
        receipt_data = {
            "Taxation": settings.TBANK_TAXATION,
            "Email": email,
            "Items": [
                {
                    "Name": f"Subscription: {tier.name}",
                    "Price": amount_cents,
                    "Quantity": 1,
                    "Amount": amount_cents,
                    "Tax": "none",  # "none" implies no VAT (common for USN). Use "vat20" if you pay VAT.
                    "PaymentMethod": "full_prepayment",
                    "PaymentObject": "service"
                }
            ]
        }

    try:
        # 4. Call TBank with Receipt
        payment_url, tbank_id = await tbank_service.init_payment(
            order_id=str(payment.id),
            amount_cents=amount_cents,
            description=f"Subscription: {tier.name}",
            user_id=str(user.id),
            recurrent=True,
            receipt=receipt_data
        )

        # 5. Update External ID
        payment.tbank_payment_id = tbank_id
        session.add(payment)
        await session.commit()

        # CHANGED: Return payment_id so the frontend can poll /status
        return {
            "payment_url": payment_url,
            "payment_id": str(payment.id)
        }

    except Exception as e:
        payment.tbank_status = "ERROR"
        session.add(payment)
        await session.commit()
        raise HTTPException(status_code=500, detail=str(e))


@payments.get("/status/{payment_id}")
async def check_payment_status(
        payment_id: uuid.UUID,
        session: AsyncSession = Depends(get_session)
):
    """
    Frontend polls this endpoint to check if the payment was confirmed.
    """
    payment = await session.get(Payment, payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    return {
        "id": str(payment.id),
        "status": payment.tbank_status,
        "is_confirmed": payment.tbank_status == "CONFIRMED",
        "tier_name": payment.tier_name
    }


@payments.post("/webhook")
async def tbank_webhook(request: Request, session: AsyncSession = Depends(get_session)):
    data = await request.json()

    # 1. Security Check
    if not tbank_service.verify_notification(data):
        settings.logger.error(f"Webhook signature verification failed. Data: {data}")
        return Response(content="OK", media_type="text/plain")

    order_id = data.get("OrderId")
    new_status = data.get("Status")
    success = data.get("Success", False)

    try:
        payment_uuid = uuid.UUID(order_id)

        # 2. Find Payment with ROW LOCK
        # 'with_for_update()' ensures no other request can modify this row
        # until this transaction commits. This prevents !!race conditions!!.
        query = select(Payment).where(Payment.id == payment_uuid).with_for_update()
        result = await session.exec(query)
        payment = result.first()

    except ValueError:
        return Response(content="OK", media_type="text/plain")

    if not payment:
        return Response(content="OK", media_type="text/plain")

    # 3. State Guard Logic
    # If we are already in a final state, ignore updates.
    current_status = payment.tbank_status

    FINAL_STATES = {"CONFIRMED", "CANCELED", "REJECTED", "REFUNDED"}

    if current_status in FINAL_STATES:
        # Ignore "late" webhooks like AUTHORIZED if we are already confirmed
        return Response(content="OK", media_type="text/plain")

    # If the new status is AUTHORIZED, but we are actively processing CONFIRMED elsewhere,
    # the lock prevents it. If we haven't processed CONFIRMED yet, AUTHORIZED is fine.

    payment.tbank_status = new_status
    session.add(payment)

    # 4. Handle Success
    if new_status == "CONFIRMED" and success:
        try:
            await save_payment_method(session, payment.user_id)
        except Exception as e:
            logger.error(f"Failed to save payment method: {e}")

        settings.custom_logger.info(f"Payment {payment_uuid} confirmed! Adding subscription...")
        await activate_subscription(session, payment)
        settings.custom_logger.info('Subscription added!')

    await session.commit()
    return Response(content="OK", media_type="text/plain")


async def activate_subscription(session: AsyncSession, payment: Payment):
    """
    Cancels old active subscriptions and creates a new one.
    """
    # 1. Fetch the new tier details
    tier = (await session.exec(select(SubscriptionTier).where(SubscriptionTier.name == payment.tier_name))).first()
    if not tier:
        print(f"Tier {payment.tier_name} not found during activation")
        return

    # 2. Find ANY currently active subscriptions for this user
    query = select(UserSubscription).where(
        UserSubscription.user_id == payment.user_id,
        UserSubscription.status == SubscriptionStatus.active
    )
    active_subs = (await session.exec(query)).all()

    # 3. Cancel/Expire them immediately
    # This ensures the /api/v1/user/subscription/active endpoint only ever finds the NEW one
    for sub in active_subs:
        sub.status = SubscriptionStatus.cancelled
        # Optional: Set expires_at to now to double-ensure logic everywhere handles it
        # sub.expires_at = datetime.now(timezone.utc).replace(tzinfo=None)()
        session.add(sub)

    # 4. Create the NEW Subscription
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    expires_at = now + relativedelta(months=1)

    new_sub = UserSubscription(
        user_id=payment.user_id,
        tier_id=tier.id,
        status=SubscriptionStatus.active,
        started_at=now,
        expires_at=expires_at
    )
    session.add(new_sub)


async def save_payment_method(session: AsyncSession, user_id: uuid.UUID):
    """
    Fetches user's cards from TBank and saves the newest RebillId.
    """
    # 1. Fetch cards from TBank
    cards = await tbank_service.get_card_list(str(user_id))
    if not cards:
        return

    # 2. Get the most recent card (usually the last one added)
    # TBank returns them in order, or we can check CardId logic depending on their API version.
    # For now, taking the last one is safe for a "just added" card.
    latest_card = cards[-1]

    rebill_id = str(latest_card.get("RebillId"))
    pan = latest_card.get("Pan", "****")
    card_type = latest_card.get("CardType", "Unknown")  # e.g. 0=Debit, 1=Credit, etc. or text
    exp = latest_card.get("ExpDate", "")

    # 3. Check if we already have this RebillId
    existing = await session.exec(select(PaymentMethod).where(PaymentMethod.rebill_id == rebill_id))
    if existing.first():
        return

    # 4. Save new method
    # Optional: Mark others as not default if you want only one active
    # await session.exec(update(PaymentMethod).where(PaymentMethod.user_id == user_id).values(is_default=False))

    method = PaymentMethod(
        user_id=user_id,
        rebill_id=rebill_id,
        pan=pan,
        card_type=str(card_type),
        exp_date=exp,
        is_default=True
    )
    session.add(method)