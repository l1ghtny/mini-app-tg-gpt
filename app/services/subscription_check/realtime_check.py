from openai.types.responses import FileSearchToolParam, WebSearchTool
from openai.types.responses.tool import ImageGeneration

from app.services.model_registry import canonicalize_image_model
from app.services.subscription_check.entitlements import get_active_tier


async def check_tier(current_user, session):
    tier = await get_active_tier(session, current_user.id)
    return tier


async def create_tools_list(
    image_allowed: bool,
    image_model: str = "gpt-image-1-mini",
    image_quality: str | None = None,
    image_size: str | None = None,
    vector_store_ids: list[str] | None = None,
    provider: str = "openai",
):
    base_tools = [WebSearchTool(type="web_search")]

    if vector_store_ids and provider == "openai":
        base_tools.append(
            FileSearchToolParam(type="file_search", vector_store_ids=vector_store_ids)
        )

    if image_allowed and provider in {"openai", "google"}:
        image_model = canonicalize_image_model(image_model)
        if provider == "google":
            base_tools.append(
                {
                    "type": "image_generation",
                    "model": image_model,
                    "image_size": image_size or "1k",
                    "moderation": "low",
                }
            )
        else:
            quality = image_quality or ("auto" if image_model == "gpt-image-1.5" else "medium")
            base_tools.append(
                ImageGeneration(
                    type="image_generation",
                    model=image_model,
                    quality=quality,
                    moderation="low",
                    partial_images=2,
                )
            )

    return base_tools
