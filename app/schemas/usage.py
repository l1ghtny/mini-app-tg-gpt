from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel

from app.schemas.subscriptions import UsagePackResponse


class TextEntitlementEntry(BaseModel):
    kind: Literal["tier", "pack"]
    source: Literal["subscription", "paid", "free"]
    tier_id: Optional[str] = None
    usage_pack_id: Optional[str] = None
    pack_id: Optional[str] = None
    name: Optional[str] = None
    cap: int
    used: int
    remaining: int
    expires_at: Optional[datetime] = None
    purchased_at: Optional[datetime] = None


class TextModelUsage(BaseModel):
    model: str
    total_remaining: int
    selected: Optional[TextEntitlementEntry] = None
    entitlements: list[TextEntitlementEntry] = []


class UserTextUsageResponse(BaseModel):
    status: Literal["none", "active"]
    models: list[TextModelUsage]


class ImageFeatureUsage(BaseModel):
    cap: int
    used: int
    remaining: int


class FeatureUsageResponse(BaseModel):
    status: Literal["none", "active"]
    features: dict[str, ImageFeatureUsage]


class ImagePacing(BaseModel):
    is_throttled: bool
    wait_seconds: int


class ImageEnergyBalance(BaseModel):
    daily_energy: int
    max_energy: int
    available_energy: int
    saved_energy: int
    used_energy: int
    saved_days: int


class ImageEntitlementEntry(BaseModel):
    kind: Literal["tier", "pack"]
    source: Literal["subscription", "paid", "free"]
    tier_id: Optional[str] = None
    usage_pack_id: Optional[str] = None
    pack_id: Optional[str] = None
    name: Optional[str] = None
    cap: float
    used: float
    remaining_credits: float
    daily_image_energy: Optional[int] = None
    energy_balance: Optional[ImageEnergyBalance] = None
    expires_at: Optional[datetime] = None
    purchased_at: Optional[datetime] = None
    pacing: Optional[ImagePacing] = None


class ImageSourceUsage(BaseModel):
    kind: Literal["tier", "pack"]
    source: Literal["subscription", "paid", "free"]
    tier_id: Optional[str] = None
    usage_pack_id: Optional[str] = None
    cap: Optional[float] = None
    used: Optional[float] = None
    remaining: int
    remaining_credits: float
    pacing: Optional[ImagePacing] = None


class ImageQualityUsage(BaseModel):
    quality: str
    credit_cost: float
    description: Optional[str] = None
    remaining: int
    remaining_credits: float
    sources: list[ImageSourceUsage] = []


class ImageModelUsage(BaseModel):
    model: str
    entitlements: list[ImageEntitlementEntry] = []
    total_remaining_credits: float
    qualities: list[ImageQualityUsage] = []


class UserImageUsageResponse(BaseModel):
    status: Literal["none", "active"]
    models: list[ImageModelUsage]


class ImageEnergySourceUsage(BaseModel):
    kind: Literal["tier"]
    source: Literal["subscription", "paid", "free"]
    tier_id: str
    tier_name: str
    daily_energy: int
    max_energy: int
    available_energy: int
    saved_energy: int
    used_energy: int
    saved_days: int
    is_throttled: bool
    wait_seconds: int


class UserImageEnergyResponse(BaseModel):
    status: Literal["none", "active"]
    total_daily_energy: int = 0
    total_max_energy: int = 0
    total_available_energy: int = 0
    total_saved_energy: int = 0
    total_used_energy: int = 0
    sources: list[ImageEnergySourceUsage] = []


class UsagePackBalanceInfo(BaseModel):
    pack_id: str
    name: str
    total_credits: float
    used_credits: float
    remaining_credits: float
    expires_at: Optional[datetime] = None
    purchased_at: Optional[datetime] = None
    pack_details: UsagePackResponse


class UsageBalanceResponse(BaseModel):
    active_packs_count: int
    label: str
    packs: list[UsagePackBalanceInfo] = []
