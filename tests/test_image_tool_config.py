import pytest

from app.services.subscription_check.realtime_check import create_tools_list


@pytest.mark.asyncio
async def test_create_tools_list_requests_partial_images_when_image_tool_is_enabled():
    tools = await create_tools_list(
        image_allowed=True,
        image_model="gpt-image-1.5",
        image_quality="high",
    )

    image_tool = next(tool for tool in tools if tool.type == "image_generation")
    assert image_tool.partial_images == 2
    assert image_tool.quality == "high"
