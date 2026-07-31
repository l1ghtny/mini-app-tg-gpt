from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


OnboardingItemId = Literal[
    "welcome",
    "try_folders",
    "open_on_desktop",
    "desktop_fullscreen_hint",
]


class OnboardingItemState(BaseModel):
    seen_at: Optional[datetime] = None
    dismissed_at: Optional[datetime] = None


class OnboardingEvent(BaseModel):
    item: OnboardingItemId
    action: Literal["seen", "dismissed"]


class UserSettingsResponse(BaseModel):
    default_text_model: str
    default_image_model: str
    default_document_provider: str = "openai"
    default_thinking: bool = True
    onboarding_state: dict[OnboardingItemId, OnboardingItemState] = Field(
        default_factory=dict
    )


class UpdateUserSettingsRequest(BaseModel):
    default_text_model: Optional[str] = None
    default_image_model: Optional[str] = None
    default_document_provider: Optional[str] = None
    default_thinking: Optional[bool] = None
    onboarding_events: list[OnboardingEvent] = Field(default_factory=list, max_length=8)
