from typing import Dict, List

from pydantic import BaseModel


class SubscriptionResponse(BaseModel):
    subscription_id: str
    status: str
    started_at: str
    expires_at: str
    discount_percent: int
    discount_expires_at: str
    tier_name: str
    tier_description: str


class TierMonthlyLimits(BaseModel):
    model_name: str
    requests_limit: int


class AccessCodeResponse(BaseModel):
    id: str
    code: str
    tier_name: str
    tier_price: int
    tier_monthly_images: int
    tier_monthly_limits: List[TierMonthlyLimits]
    discount_percent: int
    discount_months: int