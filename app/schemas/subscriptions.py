from typing import Dict, List, Optional

from pydantic import BaseModel


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



class TierMonthlyLimits(BaseModel):
    model_name: str
    requests_limit: int


class SubscriptionTierResponse(BaseModel):
    name: str
    name_ru: str
    description: str
    description_ru: str
    price_cents: int
    monthly_images: int
    tier_model_limits: List[TierMonthlyLimits]


class InitPaymentRequest(BaseModel):
    tier_name: str
    email: str
