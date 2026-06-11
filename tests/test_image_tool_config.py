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


@pytest.mark.asyncio
async def test_create_tools_list_skips_file_search_for_google_provider():
    tools = await create_tools_list(
        image_allowed=True,
        image_model="gemini-3.1-flash-image-preview",
        image_size="1k",
        vector_store_ids=["vs_123"],
        provider="google",
    )

    assert any(tool.type == "web_search" for tool in tools)
    assert any((tool.get("type") if isinstance(tool, dict) else tool.type) == "image_generation" for tool in tools)
    assert all((tool.get("type") if isinstance(tool, dict) else tool.type) != "file_search" for tool in tools)
