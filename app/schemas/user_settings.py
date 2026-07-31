from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


OnboardingItemId = Literal[
    "welcome",
    "first_chat_guide",
    "first_chat_workflow",
    "first_chat_prompt",
    "first_chat_send",
    "try_folders",
    "open_on_desktop",
    "desktop_fullscreen_hint",
]

OnboardingSurface = Literal[
    "telegram_mobile",
    "telegram_desktop",
    "web_mobile",
    "web_desktop",
]

OnboardingWorkflow = Literal[
    "quick_answer",
    "writing_analysis",
    "compare_decide",
    "research_sources",
    "document_analysis",
]


class OnboardingItemState(BaseModel):
    seen_at: Optional[datetime] = None
    dismissed_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    flow_version: Optional[int] = None
    surface: Optional[OnboardingSurface] = None
    choice: Optional[OnboardingWorkflow] = None


class OnboardingEvent(BaseModel):
    item: OnboardingItemId
    action: Literal["seen", "dismissed", "completed"]
    flow_version: Optional[int] = Field(default=None, ge=1, le=100)
    surface: Optional[OnboardingSurface] = None
    choice: Optional[OnboardingWorkflow] = None


class UserSettingsResponse(BaseModel):
    language: Optional[Literal["en", "ru"]] = None
    default_text_model: str
    default_image_model: str
    default_document_provider: str = "openai"
    default_thinking: bool = True
    onboarding_state: dict[OnboardingItemId, OnboardingItemState] = Field(
        default_factory=dict
    )


class UpdateUserSettingsRequest(BaseModel):
    language: Optional[Literal["en", "ru"]] = None
    default_text_model: Optional[str] = None
    default_image_model: Optional[str] = None
    default_document_provider: Optional[str] = None
    default_thinking: Optional[bool] = None
    onboarding_events: list[OnboardingEvent] = Field(default_factory=list, max_length=8)
