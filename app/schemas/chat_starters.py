from typing import Literal

from pydantic import BaseModel, Field


ChatStarterLang = Literal["en", "ru"]


class ChatStarterSuggestionsRequest(BaseModel):
    count: int = Field(default=4, ge=1, le=20)
    language: ChatStarterLang = "en"


class ChatStarterSuggestionResponse(BaseModel):
    text: str


class ChatStarterSuggestionsResponse(BaseModel):
    language: ChatStarterLang
    count: int
    items: list[ChatStarterSuggestionResponse] = Field(default_factory=list)
