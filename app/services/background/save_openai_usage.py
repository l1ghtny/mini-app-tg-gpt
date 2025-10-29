import uuid
from typing import Optional

from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import TokenUsage
from app.services.pricing_service import PricingService


async def log_usage(
    session: AsyncSession,
    *,
    user_id: Optional[uuid.UUID],
    conversation_id: Optional[uuid.UUID],
    request_id: str,
    provider: str,
    model_name: str,
    status: str,
    error_message: Optional[str],
    input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int,
    web_search_calls: int,
    images_generated: int,
) -> None:
    pricing = PricingService(session)

    (
        currency,
        cost_input,
        cost_output,
        cost_reasoning,
        cost_web_search,
        cost_images,
        total_cost,
    ) = await pricing.compute_costs(
        provider,
        model_name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        web_search_calls=web_search_calls,
        images_generated=images_generated,
    )

    usage_row = TokenUsage(
        user_id=user_id,
        conversation_id=conversation_id,
        provider=provider,
        model_name=model_name,
        request_id=request_id,
        status=status,
        error_message=error_message,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        web_search_calls=web_search_calls,
        images_generated=images_generated,
        currency=currency,
        cost_input=cost_input,
        cost_output=cost_output,
        cost_reasoning=cost_reasoning,
        cost_web_search=cost_web_search,
        cost_images=cost_images,
        total_cost=total_cost,
    )

    session.add(usage_row)

    await session.commit()