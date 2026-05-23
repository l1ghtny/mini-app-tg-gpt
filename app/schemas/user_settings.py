from typing import Optional
from pydantic import BaseModel


class UserSettingsResponse(BaseModel):
    default_text_model: str
    default_image_model: str


class UpdateUserSettingsRequest(BaseModel):
    default_text_model: Optional[str] = None
    default_image_model: Optional[str] = None
