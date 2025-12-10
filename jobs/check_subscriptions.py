import asyncio
import logging
import sys
import os
import uuid
from datetime import datetime, timedelta, timezone

# Ensure we can import 'app'
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import selectinload
from sqlmodel import select, desc
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.db.database import engine
from app.db.subscription_tiers import UserSubscription, SubscriptionTier, SubscriptionStatus
from app.db.models import PaymentMethod, Payment
from app.services.banking.tbank import tbank_service

# Setup Logging
logger = getattr(settings, "custom_logger", logging.getLogger("subscription_job"))

FREE_TIER_NAME = "Free"
MAX_RETRIES = 3  # Days/Attempts to keep trying before downgrading


async def execute_recurring_charge(session: AsyncSession, user_id: uuid.UUID, amount_cents: int, description: str,
                                   rebill_id: str) -> bool:
    """
    Executes the TBank charge logic using a specific RebillId.
    Returns True if TBank accepted the request (Init successful).
    Returns False if TBank rejected it immediately (Error).
    """
    # Create local Payment record
    payment = Payment(
        user_id=user_id,
        tier_name=description.replace("Subscription: ", ""),
        amount=amount_cents,
        tbank_status="RECURRING_INIT"
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)

    try:
        # 1. INIT
        payment_url, tbank_payment_id = await tbank_service.init_payment(
            order_id=str(payment.id),
            amount_cents=amount_cents,
            description=description,
            user_id=str(user_id),
            recurrent=False
        )

        payment.tbank_payment_id = tbank_payment_id
        session.add(payment)
        await session.commit()

        # 2. CHARGE
        await tbank_service.charge(payment_id=tbank_payment_id, rebill_id=rebill_id)

        logger.info(f"User {user_id}: Recurring charge initiated (Payment {payment.id})")
        return True

    except Exception as e:
        logger.error(f"User {user_id}: Recurring charge failed: {e}")
        payment.tbank_status = "ERROR"
        session.add(payment)
        await session.commit()
        return False


async def main():
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        logger.info("Starting subscription check...")

        # 1. Fetch Free Tier
        result = await session.exec(select(SubscriptionTier).where(SubscriptionTier.name == FREE_TIER_NAME))
        free_tier = result.first()

        if not free_tier:
            logger.error(f"CRITICAL: Free tier '{FREE_TIER_NAME}' not found.")
            return

        # 2. Find Expired Active Subscriptions
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        query = (
            select(UserSubscription, SubscriptionTier)
            .join(SubscriptionTier, UserSubscription.tier_id == SubscriptionTier.id)
            .where(
                UserSubscription.status == SubscriptionStatus.active,
                UserSubscription.expires_at is not None,
                UserSubscription.expires_at < now
            )
        )

        result = await session.exec(query)
        expired_subs_with_tiers = result.all()

        logger.info(f"Found {len(expired_subs_with_tiers)} expired subscriptions.")

        for old_sub, tier in expired_subs_with_tiers:
            try:
                downgrade_needed = True  # Default assumption

                # --- PAID TIER LOGIC ---
                if tier.price_cents > 0:
                    logger.info(f"User {old_sub.user_id}: Paid tier '{tier.name}' expired.")

                    # A. Check for Saved Card
                    pm_query = (
                        select(PaymentMethod)
                        .where(PaymentMethod.user_id == old_sub.user_id, PaymentMethod.is_default == True)
                        .order_by(desc(PaymentMethod.created_at))
                    )
                    pm_result = await session.exec(pm_query)
                    payment_method = pm_result.first()

                    if payment_method:
                        # B. Check Retry Count (How many times did we try in the last X days?)
                        # We look for payments created recently that are NOT confirmed.
                        retry_window = now - timedelta(days=MAX_RETRIES + 1)

                        attempts_query = select(Payment).where(
                            Payment.user_id == old_sub.user_id,
                            Payment.tier_name == tier.name,
                            Payment.created_at > retry_window
                        )
                        attempts = (await session.exec(attempts_query)).all()

                        if len(attempts) < MAX_RETRIES:
                            logger.info(
                                f"User {old_sub.user_id}: Attempt {len(attempts) + 1}/{MAX_RETRIES}. Executing charge...")

                            # C. Try to Charge
                            await execute_recurring_charge(
                                session,
                                old_sub.user_id,
                                int(tier.price_cents * 100),
                                f"Subscription: {tier.name}",
                                payment_method.rebill_id
                            )

                            # D. EXTEND GRACE PERIOD (Crucial Step)
                            # Regardless of whether the API call succeeded or errored above,
                            # we extend the user by 1 day to give them a chance.
                            # If it succeeds, the webhook will extend for real.
                            # If it failed, we'll try again tomorrow (until MAX_RETRIES).
                            old_sub.expires_at = now + timedelta(days=1)
                            session.add(old_sub)
                            downgrade_needed = False  # Don't downgrade yet
                        else:
                            logger.info(f"User {old_sub.user_id}: Max retries ({MAX_RETRIES}) reached. Giving up.")
                    else:
                        logger.info(f"User {old_sub.user_id}: No saved card found.")

                # --- DOWNGRADE FLOW ---
                if downgrade_needed:
                    logger.info(f"User {old_sub.user_id}: Subscription expired. No fallback.")

                    # Mark as expired
                    old_sub.status = SubscriptionStatus.expired
                    session.add(old_sub)

            except Exception as e:
                logger.error(f"Failed to process user {old_sub.user_id}: {e}")

        await session.commit()
        logger.info("Subscription check complete.")


if __name__ == "__main__":
    asyncio.run(main())