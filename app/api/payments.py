import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api import payment_helpers
from app.api.dependencies import get_current_user
from app.db.database import get_session
from app.db.models import AppUser
from app.schemas.subscriptions import (
    BoundSubscriptionChargeRequest,
    BoundSubscriptionChargeResponse,
    CurrentSubscriptionRefundResponse,
    CurrentSubscriptionRefundStatusResponse,
    InitPaymentRequest,
    InitUsagePackPaymentRequest,
    PaymentInitResponse,
    PaymentMethodResponse,
    PaymentMethodsResponse,
    PaymentStatusResponse,
    MockUsagePackPurchaseRequest,
    SubscriptionBindingInitRequest,
    SubscriptionBindingInitResponse,
    SubscriptionBindingStatusResponse,
    UserAgreementResponse,
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


@payments.post("/bind-init", response_model=SubscriptionBindingInitResponse)
async def init_subscription_binding(
    payload: SubscriptionBindingInitRequest,
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await payment_helpers.init_subscription_binding(session, user, payload)


@payments.get("/bind-status/{binding_id}", response_model=SubscriptionBindingStatusResponse)
async def get_subscription_binding_status(
    binding_id: uuid.UUID,
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await payment_helpers.get_subscription_binding_status(session, user, binding_id)


@payments.post("/activate-bound", response_model=BoundSubscriptionChargeResponse)
async def activate_bound_subscription(
    payload: BoundSubscriptionChargeRequest,
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await payment_helpers.charge_bound_subscription(session, user, payload)


@payments.get("/status/{payment_id}", response_model=PaymentStatusResponse)
async def check_payment_status(
    payment_id: uuid.UUID,
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await payment_helpers.get_payment_status(session, payment_id, user)


@payments.get("/refund-status", response_model=CurrentSubscriptionRefundStatusResponse)
async def get_current_subscription_refund_status(
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await payment_helpers.get_current_subscription_refund_status(session, user)


@payments.post("/refund-current-subscription", response_model=CurrentSubscriptionRefundResponse)
async def refund_current_subscription(
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await payment_helpers.refund_current_subscription(session, user)


@payments.get("/user-agreement", response_model=UserAgreementResponse)
async def get_user_agreement():
    return await payment_helpers.get_user_agreement()


@payments.get("/payment-methods", response_model=PaymentMethodsResponse)
async def list_payment_methods(
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await payment_helpers.list_payment_methods(session, user)


@payments.post("/payment-methods/{payment_method_id}/default", response_model=PaymentMethodResponse)
async def set_default_payment_method(
    payment_method_id: uuid.UUID,
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await payment_helpers.set_default_payment_method(session, user, payment_method_id)


@payments.delete("/payment-methods/{payment_method_id}", response_class=Response, status_code=204)
async def detach_payment_method(
    payment_method_id: uuid.UUID,
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await payment_helpers.detach_payment_method(session, user, payment_method_id)


@payments.post("/retry-renewal", response_model=BoundSubscriptionChargeResponse)
async def retry_subscription_renewal(
    payment_method_id: uuid.UUID | None = None,
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await payment_helpers.retry_subscription_renewal(session, user, payment_method_id)


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
