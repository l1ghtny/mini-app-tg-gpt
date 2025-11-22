import uuid
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, conint

from app.schemas.subscriptions import SubscriptionResponse, SubscriptionTierResponse


class AccessCodeDiscountIn(BaseModel):
    tier_id: str
    percent: conint(ge=0, le=100)
    duration_months: int | None = None  # null = unlimited

class AccessCodeCreate(BaseModel):
    code: str
    max_uses: int | None = None
    expires_at: datetime | None = None
    grant_tier_id: str | None = None        # beta_tester or whatever
    discounts: list[AccessCodeDiscountIn] = []


class AccessCodeDiscountOut(BaseModel):
    id: uuid.UUID
    tier_id: uuid.UUID
    tier_name: str
    discount_percent: int
    duration_months: Optional[int]


class AccessCodeResponse(BaseModel):
    id: uuid.UUID
    code: str

    tier: Optional[SubscriptionTierResponse] = None
    discounts: List[AccessCodeDiscountOut] = []

    max_uses: Optional[int] = None
    expires_at: Optional[datetime] = None

    class Config:
        from_attributes = True