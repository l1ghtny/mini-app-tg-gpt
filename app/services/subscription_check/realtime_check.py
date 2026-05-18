from openai.types.responses import FileSearchToolParam, WebSearchTool
from openai.types.responses.tool import ImageGeneration

from app.services.subscription_check.entitlements import get_active_tier


async def check_tier(current_user, session):
    tier = await get_active_tier(session, current_user.id)
    return tier


async def create_tools_list(
    image_allowed: bool,
    image_model: str = "gpt-image-1-mini",
    image_quality: str | None = None,
    vector_store_ids: list[str] | None = None,
):
    base_tools = [WebSearchTool(type="web_search")]

    if vector_store_ids:
        base_tools.append(
            FileSearchToolParam(type="file_search", vector_store_ids=vector_store_ids)
        )

    if image_allowed:
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

