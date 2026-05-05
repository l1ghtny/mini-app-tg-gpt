from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


WhatsNewLang = Literal["en", "ru"]
WhatsNewKind = Literal["feature", "improvement", "fix", "announcement", "promo"]
WhatsNewCtaKind = Literal["open_settings", "open_subscription", "open_url", "dismiss"]


class WhatsNewCTA(BaseModel):
    label: str
    kind: WhatsNewCtaKind
    value: Optional[str] = None


class WhatsNewAudience(BaseModel):
    plan: list[Literal["free", "pro"]] = Field(default_factory=list)
    min_app_version: Optional[str] = None


class WhatsNewItemResponse(BaseModel):
    id: str
    published_at: datetime
    kind: WhatsNewKind
    title: str
    body: str
    icon: Optional[str] = None
    image_url: Optional[str] = None
    cta: Optional[WhatsNewCTA] = None
    audience: Optional[WhatsNewAudience] = None
    pinned: bool = False


class WhatsNewListResponse(BaseModel):
    items: list[WhatsNewItemResponse] = Field(default_factory=list)
    latest_published_at: Optional[datetime] = None
    seen_up_to: Optional[datetime] = None
    has_unseen: bool = False
    unseen_count: int = 0


class WhatsNewSeenRequest(BaseModel):
    ids: Optional[list[str]] = None
    up_to: Optional[datetime] = None

    @model_validator(mode="after")
    def _validate_payload(self) -> "WhatsNewSeenRequest":
        ids = self.ids or []
        if not ids and self.up_to is None:
            raise ValueError("Provide either `ids` or `up_to`.")
        return self


class WhatsNewSeenResponse(BaseModel):
    seen_up_to: Optional[datetime] = None

