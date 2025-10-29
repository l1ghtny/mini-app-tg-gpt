from decimal import Decimal
from typing import Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.models import AiModelPricing

ONE_MILLION = Decimal(1_000_000)

class PricingService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_pricing(self, provider: str, model_name: str) -> Optional[AiModelPricing]:
        result = await self.session.execute(
            select(AiModelPricing).where(
                AiModelPricing.provider == provider,
                AiModelPricing.model_name == model_name,
                AiModelPricing.is_active == True,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    def cost_per_1m(price_per_1m: Decimal, tokens: int) -> Decimal:
        if not price_per_1m or tokens <= 0:
            return Decimal("0")
        return (price_per_1m * Decimal(tokens) / ONE_MILLION).quantize(Decimal("0.000001"))

    async def compute_costs(
        self,
        provider: str,
        model_name: str,
        *,
        input_tokens: int,
        output_tokens: int,
        reasoning_tokens: int,
        web_search_calls: int,
        images_generated: int,
    ) -> Tuple[str, Decimal, Decimal, Decimal, Decimal, Decimal, Decimal]:
        pricing = await self.get_pricing(provider, model_name)
        currency = pricing.currency if pricing else "USD"
        ui = pricing.unit_price_input_per_1m if pricing else Decimal("0")
        uo = pricing.unit_price_output_per_1m if pricing else Decimal("0")
        ur = pricing.unit_price_reasoning_per_1m if pricing else Decimal("0")
        us = pricing.unit_price_web_search_call if pricing else Decimal("0")
        im = pricing.unit_price_image_generation if pricing else Decimal("0")

        cost_input = self.cost_per_1m(ui, input_tokens)
        cost_output = self.cost_per_1m(uo, output_tokens)
        cost_reasoning = self.cost_per_1m(ur, reasoning_tokens)
        cost_web_search = (us * Decimal(web_search_calls)).quantize(Decimal("0.000001")) if us else Decimal("0")
        cost_images = (im * Decimal(images_generated)).quantize(Decimal("0.000001")) if im else Decimal("0")
        total = (cost_input + cost_output + cost_reasoning + cost_web_search + cost_images).quantize(Decimal("0.000001"))

        return currency, cost_input, cost_output, cost_reasoning, cost_web_search, cost_images, total