import uuid
from datetime import datetime
from typing import Literal, Optional, List

from pydantic import BaseModel, conint

from app.schemas.subscriptions import SubscriptionResponse, SubscriptionTierResponse, UsagePackResponse


class AccessCodeDiscountIn(BaseModel):
    tier_id: str
    percent: conint(ge=0, le=100)
    duration_months: int | None = None  # null = unlimited

class AccessCodeCreate(BaseModel):
    code: str
    max_uses: int | None = None
    expires_at: datetime | None = None
    grant_tier_id: str | None = None        # beta_tester or whatever
    grant_usage_pack_id: str | None = None
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
    usage_pack: Optional[UsagePackResponse] = None
    discounts: List[AccessCodeDiscountOut] = []

    max_uses: Optional[int] = None
    expires_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AccessCodeRedeemResponse(BaseModel):
    status: Literal["ok"]


class AccessCodeAdminResponse(BaseModel):
    id: uuid.UUID
    code: str
    tier_id: Optional[uuid.UUID] = None
    usage_pack_id: Optional[uuid.UUID] = None
    tier_expires_in_days: int
    max_uses: int
    used_count: int
    expires_at: Optional[datetime] = None
