from datetime import datetime

from pydantic import BaseModel, conint


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