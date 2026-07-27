from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.models_catalog import ModelsCatalogResponse
from app.schemas.subscriptions import SubscriptionTierResponse, UsagePackResponse


class RequestBillingContract(BaseModel):
    unit: str = "completed_text_answer"
    units_per_completed_text_answer: int = 1
    tokens_visible_to_user: bool = False
    failed_text_requests_refunded: bool = True
    images_use_separate_energy: bool = True
    summary: str = "One completed text answer uses one request."
    summary_ru: str = "Один готовый текстовый ответ расходует один запрос."


class PublicProductCatalogResponse(BaseModel):
    generated_at: datetime
    billing_contract: RequestBillingContract = Field(default_factory=RequestBillingContract)
    models: ModelsCatalogResponse
    tiers: list[SubscriptionTierResponse]
    usage_packs: list[UsagePackResponse]
