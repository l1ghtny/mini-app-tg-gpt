from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class PersonalizationProfileResponse(BaseModel):
    main_user_prompt: str
    personalization_completed_at: Optional[datetime] = None
    personalization_dismissed_at: Optional[datetime] = None
    personalization_updated_at: Optional[datetime] = None
    show_personalization_prompt: bool
    next_prompt_at: Optional[datetime] = None
    wizard_version: str


class UpdatePersonalizationRequest(BaseModel):
    main_user_prompt: str
    source: Literal["manual", "wizard"] = "manual"
    answers: Optional[dict[str, str]] = None


class PersonalizationDismissResponse(BaseModel):
    status: Literal["ok"]
    next_prompt_at: datetime


class PersonalizationSkipResponse(BaseModel):
    status: Literal["ok"]


class WizardOptionResponse(BaseModel):
    id: str
    label: str
    label_ru: str


class WizardQuestionResponse(BaseModel):
    id: str
    title: str
    title_ru: str
    options: list[WizardOptionResponse]


class PersonalizationWizardResponse(BaseModel):
    version: str
    title: str
    title_ru: str
    description: str
    description_ru: str
    questions: list[WizardQuestionResponse]


class WizardAnswer(BaseModel):
    question_id: str
    option_id: str


class PersonalizationComposeRequest(BaseModel):
    answers: list[WizardAnswer]
    language: Literal["en", "ru"] = "en"


class PersonalizationComposeResponse(BaseModel):
    composed_prompt: str
    answers: dict[str, str]
    version: str
