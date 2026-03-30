from typing import Dict, List, Optional

from pydantic import BaseModel
from typing import Literal


class SubscriptionResponse(BaseModel):
    subscription_id: str
    status: str
    started_at: str
    expires_at: Optional[str]
    tier_name: str
    tier_name_ru: str
    tier_description: str
    tier_description_ru: str
    tier_price: int
    tier_id: str


class ActiveSubscriptionsResponse(BaseModel):
    active_subscriptions: List[SubscriptionResponse]
    primary_subscription_id: Optional[str] = None


class TierMonthlyLimits(BaseModel):
    model_name: str
    requests_limit: int


class TierImageModelLimits(BaseModel):
    image_model: str
    requests_limit: int


class ImageQualityPricingResponse(BaseModel):
    image_model: str
    quality: str
    credit_cost: float
    description: Optional[str] = None


class SubscriptionTierResponse(BaseModel):
    name: str
    name_ru: str
    description: str
    description_ru: str
    price_cents: int
    monthly_images: int
    tier_model_limits: List[TierMonthlyLimits]
    tier_image_model_limits: List[TierImageModelLimits] = []
    image_quality_pricing: List[ImageQualityPricingResponse] = []
    is_recurring: bool
    daily_image_limit: int
    allowed_image_qualities: List[str] = []
    allowed_image_models: List[str] = []
    tier_id: str


class InitPaymentRequest(BaseModel):
    tier_name: str
    email: str


class UsagePackModelLimitResponse(BaseModel):
    model_name: str
    request_credits: int


class UsagePackImageModelLimitResponse(BaseModel):
    image_model: str
    credit_amount: float


class UsagePackResponse(BaseModel):
    id: str
    name: str
    name_ru: Optional[str] = None
    description: Optional[str] = None
    description_ru: Optional[str] = None
    price_cents: int
    is_active: bool
    is_public: bool
    index: int
    model_limits: List[UsagePackModelLimitResponse] = []
    image_model_limits: List[UsagePackImageModelLimitResponse] = []


class InitUsagePackPaymentRequest(BaseModel):
    pack_id: str
    email: str


class PaymentInitResponse(BaseModel):
    payment_url: str
    payment_id: str


class PaymentStatusResponse(BaseModel):
    id: str
    status: str
    is_confirmed: bool
    tier_name: str
    product_type: Literal["subscription", "usage_pack"]
    product_name: str
    pack_id: Optional[str] = None


class CancelSubscriptionResponse(BaseModel):
    status: Literal["success"]
    message: str


class TierSubscribeResponse(BaseModel):
    status: Literal["ok"]
    tier_id: str


class FullUsagePackResponse(UsagePackResponse):
    model_limits: List[UsagePackModelLimitResponse] = []
    image_model_limits: List[UsagePackImageModelLimitResponse] = []


class MockUsagePackPurchaseRequest(BaseModel):
    user_id: str
    pack_id: str
