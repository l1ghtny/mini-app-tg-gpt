from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class MessageUsage(BaseModel):
    status: Literal["unavailable", "in_progress", "pending", "complete", "failed"]
    shared_percent: float | None = None
    luna_percent: float | None = None
    estimated_min_percent: float | None = None
    estimated_max_percent: float | None = None
    maximum_percent: float | None = None
    basis: Literal["admission", "period"] | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
