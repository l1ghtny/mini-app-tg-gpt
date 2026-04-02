from openai.types.responses import WebSearchTool
from openai.types.responses.tool import ImageGeneration

from app.services.subscription_check.entitlements import get_active_tier


async def check_tier(current_user, session):
    tier = await get_active_tier(session, current_user.id)
    return tier


async def create_tools_list(
    image_allowed: bool,
    image_model: str = "gpt-image-1-mini",
    image_quality: str | None = None,
):
    base_tools = [WebSearchTool(type="web_search")]

    if image_allowed:
        # Dynamic Model Selection
        quality = image_quality or ("auto" if image_model == "gpt-image-1.5" else "medium")
        base_tools.append(ImageGeneration(
            type="image_generation",
            model=image_model,
            quality=quality,
            moderation='low',
            partial_images=2,
        ))

    return base_tools
