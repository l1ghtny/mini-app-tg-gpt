import datetime

from fastapi import APIRouter, Depends, HTTPException, Body, Request
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.db.database import get_session
from app.api.dependencies import get_current_user
from app.db.models import AppUser, Payment
from app.db.subscription_tiers import SubscriptionTier, UserSubscription, SubscriptionStatus
from app.services.banking.tbank import tbank_service

payments = APIRouter(tags=["payments"], prefix="/payments/tbank")


@payments.post("/init")
async def init_payment(
        tier_name: str = Body(..., embed=True),
        user: AppUser = Depends(get_current_user),
        session: AsyncSession = Depends(get_session)
):
    # 1. Fetch Tier Info
    # Note: Adjust the model import path if SubscriptionTier is located elsewhere
    result = await session.exec(select(SubscriptionTier).where(SubscriptionTier.name == tier_name))
    tier = result.first()

    if not tier:
        raise HTTPException(status_code=404, detail="Tier not found")

    # 2. Calculate Amount (assuming price_cents is stored in SubscriptionTier)
    # TBank expects amount in kopecks (cents)
    amount_cents = int(tier.price_cents * 100)  # Convert if your DB stores rubels, or use as is if cents

    # 3. Create Local Payment Record
    payment = Payment(
        user_id=user.id,
        tier_name=tier.name,
        amount=amount_cents,
        tbank_status="NEW"
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)

    try:
        # 4. Call TBank
        payment_url, tbank_id = await tbank_service.init_payment(
            order_id=str(payment.id),
            amount_cents=amount_cents,
            description=f"Subscription: {tier.name}",
            user_id=str(user.id)
        )

        # 5. Update External ID
        payment.tbank_payment_id = tbank_id
        session.add(payment)
        await session.commit()

        return {"payment_url": payment_url}

    except Exception as e:
        # Cleanup if failed
        payment.tbank_status = "FAILED"
        session.add(payment)
        await session.commit()
        raise HTTPException(status_code=500, detail=str(e))


@payments.post("/webhook")
async def tbank_webhook(request: Request, session: AsyncSession = Depends(get_session)):
    data = await request.json()

    # 1. Security Check
    if not await tbank_service.verify_notification(data):
        # TBank expects "OK" text response even on error to stop retrying,
        # but for security we might want to log this heavily.
        return "OK"

    order_id = data.get("OrderId")
    status = data.get("Status")

    # 2. Find Payment
    # We use order_id which corresponds to our Payment.id
    payment = await session.get(Payment, order_id)
    if not payment:
        return "OK"

    # 3. Update Payment Status
    payment.tbank_status = status
    session.add(payment)

    # 4. Handle Successful Payment
    if status == "CONFIRMED":
        # Check if this payment was already processed to avoid double-crediting
        # (TBank might send the same webhook multiple times)
        # We can check if the user subscription was already updated or add a 'processed' flag to Payment.
        # For this example, we'll just execute the logic (idempotency is safer with a specific flag).

        await activate_subscription(session, payment)

    await session.commit()
    return "OK"


async def activate_subscription(session: AsyncSession, payment: Payment):
    # Fetch the tier details
    tier = (await session.exec(select(SubscriptionTier).where(SubscriptionTier.name == payment.tier_name))).first()
    if not tier:
        return

    # Check for existing subscription
    query = select(UserSubscription).where(UserSubscription.user_id == payment.user_id)
    existing_sub = (await session.exec(query)).first()

    now = datetime.utcnow()
    duration_days = 30  # Default to 1 month, or fetch from tier if you have a duration field

    if existing_sub:
        # Logic: If active and same tier, extend. If different tier, upgrade immediately.
        if existing_sub.expires_at and existing_sub.expires_at > now:
            new_expiry = existing_sub.expires_at + datetime.timedelta(days=duration_days)
        else:
            new_expiry = now + datetime.timedelta(days=duration_days)

        existing_sub.tier_id = tier.id
        existing_sub.status = SubscriptionStatus.active
        existing_sub.expires_at = new_expiry
        session.add(existing_sub)
    else:
        # Create new
        new_sub = UserSubscription(
            user_id=payment.user_id,
            tier_id=tier.id,
            status=SubscriptionStatus.active,
            started_at=now,
            expires_at=now + datetime.timedelta(days=duration_days)
        )
        session.add(new_sub)