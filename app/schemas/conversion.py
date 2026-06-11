from typing import Literal, Optional

from pydantic import BaseModel

from app.schemas.subscriptions import (
    CurrentSubscriptionRefundStatusResponse,
    PaymentMethodResponse,
    SubscriptionDiscountResponse,
    SubscriptionResponse,
)


class ConversionPaymentMethodsSummary(BaseModel):
    methods_count: int = 0
    has_default_method: bool = False
    default_method: Optional[PaymentMethodResponse] = None
    renewal_action_hint: Literal[
        "none",
        "bind_method",
        "retry_renewal",
        "scheduled",
        "renewal_disabled",
    ] = "none"


class PremiumSampleStateResponse(BaseModel):
    status: Literal["available", "consumed", "ineligible"] = "ineligible"
    eligible: bool = False
    reason: str = "not_available"
    kinds: list[str] = []
    available_models: list[str] = []
    default_model: Optional[str] = None
    remaining_uses_today: int = 0
    next_reset_at: Optional[str] = None


class ConversionOfferSummary(BaseModel):
    primary_nudge: Literal[
        "none",
        "premium_sample",
        "discount",
        "bind_method",
        "retry_renewal",
        "refund_available",
    ] = "none"
    has_discount: bool = False
    best_discount: Optional[SubscriptionDiscountResponse] = None
    premium_sample_available: bool = False
    refund_available: bool = False
    refund_deadline_at: Optional[str] = None


class ConversionEventRequest(BaseModel):
    event: Literal[
        "premium_sample_shown",
        "premium_sample_clicked",
        "premium_sample_paywall_opened",
    ]
    kind: Optional[str] = None
    model: Optional[str] = None
    surface: Optional[str] = None
    status: Optional[str] = None


class ConversionEventResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ConversionStateResponse(BaseModel):
    campaign: Optional[str] = None
    has_sent_first_message: bool = False
    first_purchase_available: bool = False
    discounts: list[SubscriptionDiscountResponse] = []
    primary_subscription: Optional[SubscriptionResponse] = None
    payment_methods: ConversionPaymentMethodsSummary
    premium_sample: PremiumSampleStateResponse
    refund_status: Optional[CurrentSubscriptionRefundStatusResponse] = None
    offer_summary: ConversionOfferSummary
