import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api import payment_helpers
from app.api.dependencies import get_current_user
from app.db.database import get_session
from app.db.models import AppUser
from app.schemas.subscriptions import (
    InitPaymentRequest,
    InitUsagePackPaymentRequest,
    PaymentInitResponse,
    PaymentStatusResponse,
    MockUsagePackPurchaseRequest,
)
from app.core.config import settings

payments = APIRouter(tags=["payments"], prefix="/payments/tbank")


@payments.post("/init", response_model=PaymentInitResponse)
async def init_payment(
    payload: InitPaymentRequest,
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await payment_helpers.init_subscription_payment(session, user, payload)


@payments.get("/status/{payment_id}", response_model=PaymentStatusResponse)
async def check_payment_status(
    payment_id: uuid.UUID,
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await payment_helpers.get_payment_status(session, payment_id, user)


@payments.post("/init-usage-pack", response_model=PaymentInitResponse)
async def init_usage_pack_payment(
    payload: InitUsagePackPaymentRequest,
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await payment_helpers.init_usage_pack_payment(session, user, payload)


@payments.post("/webhook", response_class=Response)
async def tbank_webhook(
    background_tasks: BackgroundTasks,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    data = await request.json()
    return await payment_helpers.handle_tbank_webhook(session, background_tasks, data)


@payments.post("/mock-usage-pack-purchase", response_class=Response)
async def mock_usage_pack_purchase(
    payload: MockUsagePackPurchaseRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """
    Mocks the purchase of a usage pack for testing purposes.
    It simulates the full flow:
    1. Init payment (mocked)
    2. Webhook callback (mocked) -> activates pack
    """
    if settings.ENVIRONMENT != 'local':
        raise HTTPException(status_code=403, detail="Not allowed in production")

    return await payment_helpers.mock_usage_pack_purchase(session, background_tasks, payload)
