from fastapi import HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.metrics import track_event
from app.api import payment_helpers, user_subscription_helpers
from app.db.models import AppUser
from app.schemas.conversion import (
    ConversionEventRequest,
    ConversionEventResponse,
    ConversionOfferSummary,
    ConversionPaymentMethodsSummary,
    ConversionStateResponse,
)
from app.schemas.subscriptions import SubscriptionResponse
from app.services.premium_samples import get_premium_sample_state


def _primary_subscription_from_response(
    response,
) -> SubscriptionResponse | None:
    if not response.active_subscriptions:
        return None
    if response.primary_subscription_id:
        match = next(
            (
                sub
                for sub in response.active_subscriptions
                if sub.subscription_id == response.primary_subscription_id
            ),
            None,
        )
        if match:
            return match
    return response.active_subscriptions[0]


def _renewal_action_hint(
    primary_subscription: SubscriptionResponse | None,
) -> str:
    if primary_subscription is None:
        return "none"
    if not primary_subscription.is_recurring or not primary_subscription.auto_renew:
        return "none"
    if primary_subscription.renewal_state == "requires_method":
        return "bind_method"
    if primary_subscription.renewal_state == "grace":
        return "retry_renewal"
    if primary_subscription.renewal_state == "scheduled":
        return "scheduled"
    if primary_subscription.renewal_state == "disabled":
        return "renewal_disabled"
    return "none"


def _best_discount(
    discounts,
):
    if not discounts:
        return None
    return max(discounts, key=lambda item: int(item.percent_off or 0))


def _conversion_refund_status_or_none(refund_status):
    if refund_status is None:
        return None
    if refund_status.reason == "no_active_subscription" and refund_status.payment_id is None:
        return None
    return refund_status


def _primary_nudge(
    *,
    premium_sample_status: str,
    renewal_action_hint: str,
    best_discount,
    refund_status,
) -> str:
    if premium_sample_status == "available":
        return "premium_sample"
    if renewal_action_hint == "bind_method":
        return "bind_method"
    if renewal_action_hint == "retry_renewal":
        return "retry_renewal"
    if best_discount is not None:
        return "discount"
    if refund_status is not None and refund_status.refundable:
        return "refund_available"
    return "none"


async def get_conversion_state(
    session: AsyncSession,
    user: AppUser,
) -> ConversionStateResponse:
    discounts = await user_subscription_helpers._load_active_discounts(session, user.id)
    discounts.extend(await user_subscription_helpers._load_general_discounts(session, user.id))
    first_purchase_available = await user_subscription_helpers._first_purchase_available(session, user.id)

    try:
        subscription_response = await user_subscription_helpers.get_active_subscription(session, user)
    except HTTPException as exc:
        if exc.status_code != 403:
            raise
        subscription_response = None

    primary_subscription = (
        _primary_subscription_from_response(subscription_response)
        if subscription_response is not None
        else None
    )

    methods_response = await payment_helpers.list_payment_methods(session, user)
    default_method = next((method for method in methods_response.methods if method.is_default), None)
    renewal_action_hint = _renewal_action_hint(primary_subscription)
    premium_sample = await get_premium_sample_state(session, user_id=user.id)
    refund_status = _conversion_refund_status_or_none(
        await payment_helpers.get_current_subscription_refund_status(session, user)
    )
    best_discount = _best_discount(discounts)

    return ConversionStateResponse(
        campaign=user.campaign,
        has_sent_first_message=bool(getattr(user, "has_sent_first_message", False)),
        first_purchase_available=first_purchase_available,
        discounts=discounts,
        primary_subscription=primary_subscription,
        payment_methods=ConversionPaymentMethodsSummary(
            methods_count=len(methods_response.methods),
            has_default_method=default_method is not None,
            default_method=default_method,
            renewal_action_hint=renewal_action_hint,
        ),
        premium_sample=premium_sample,
        refund_status=refund_status,
        offer_summary=ConversionOfferSummary(
            primary_nudge=_primary_nudge(
                premium_sample_status=premium_sample.status,
                renewal_action_hint=renewal_action_hint,
                best_discount=best_discount,
                refund_status=refund_status,
            ),
            has_discount=best_discount is not None,
            best_discount=best_discount,
            premium_sample_available=premium_sample.status == "available",
            refund_available=bool(refund_status and refund_status.refundable),
            refund_deadline_at=refund_status.refund_deadline_at if refund_status else None,
        ),
    )


async def track_conversion_event_for_user(
    user: AppUser,
    request: ConversionEventRequest,
) -> ConversionEventResponse:
    tags = {
        "campaign": user.campaign or "organic",
        "kind": request.kind,
        "model": request.model,
        "surface": request.surface,
        "status": request.status,
    }
    track_event(request.event, str(user.id), tags)
    return ConversionEventResponse()
