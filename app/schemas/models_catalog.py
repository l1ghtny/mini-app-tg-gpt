from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class TierRequirementResponse(BaseModel):
    id: Optional[str] = None
    slug: Optional[str] = None
    min_rank: Optional[int] = None


class TextModelSupportsResponse(BaseModel):
    vision: bool = False
    web_search: bool = False
    file_search: bool = False
    image_gen: bool = False
    reasoning: bool = False


class TextModelCatalogEntryResponse(BaseModel):
    model_name: str
    display_name: str
    display_name_ru: Optional[str] = None
    provider: str
    tagline: Optional[str] = None
    tagline_ru: Optional[str] = None
    description: Optional[str] = None
    description_ru: Optional[str] = None
    best_for: list[str] = Field(default_factory=list)
    best_for_ru: list[str] = Field(default_factory=list)
    not_great_for: list[str] = Field(default_factory=list)
    not_great_for_ru: list[str] = Field(default_factory=list)
    speed: Optional[str] = None
    intelligence: Optional[int] = None
    context_window: Optional[int] = None
    supports: TextModelSupportsResponse = Field(default_factory=TextModelSupportsResponse)
    tier_required: Optional[TierRequirementResponse] = None
    badges: list[str] = Field(default_factory=list)
    credit_cost_hint: Optional[float] = None


class ImageModelQualityCatalogEntryResponse(BaseModel):
    quality: str
    credit_cost: float
    description: Optional[str] = None
    description_ru: Optional[str] = None


class ImageModelCatalogEntryResponse(BaseModel):
    model_name: str
    display_name: str
    display_name_ru: Optional[str] = None
    provider: str
    tagline: Optional[str] = None
    tagline_ru: Optional[str] = None
    description: Optional[str] = None
    description_ru: Optional[str] = None
    best_for: list[str] = Field(default_factory=list)
    best_for_ru: list[str] = Field(default_factory=list)
    speed: Optional[str] = None
    qualities: list[ImageModelQualityCatalogEntryResponse] = Field(default_factory=list)
    tier_required: Optional[TierRequirementResponse] = None
    badges: list[str] = Field(default_factory=list)


class ModelsCatalogResponse(BaseModel):
    text_models: list[TextModelCatalogEntryResponse] = Field(default_factory=list)
    image_models: list[ImageModelCatalogEntryResponse] = Field(default_factory=list)
    updated_at: datetime
