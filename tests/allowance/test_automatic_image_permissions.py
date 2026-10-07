from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.services import allowance_chat


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", [None, "auto", ["auto"]])
async def test_auto_tools_include_affordable_images_without_forcing_them(
    estimate_case, monkeypatch, choice
):
    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_GENERATION_V2_ENABLED", True)
    session, user, conversation, request = estimate_case
    request.tool_choice, request.required_tool = choice, None
    quote = await allowance_chat.estimate(session, user, conversation, request)
    assert "image_generation" in quote["execution_plan"]["tools"]
    assert quote["execution_plan"]["image_consent"]
    assert quote["execution_plan"]["required_tool"] is None
    assert not quote["needs_confirmation"]
    assert quote["image_quality"] == "low"
    assert quote["image_estimated_percent"] > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", [[], "none", ["web_search"]])
async def test_image_exclusions_remain_authoritative(estimate_case, monkeypatch, choice):
    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_GENERATION_V2_ENABLED", True)
    session, user, conversation, request = estimate_case
    request.tool_choice, request.required_tool = choice, None
    quote = await allowance_chat.estimate(session, user, conversation, request)
    assert "image_generation" not in quote["execution_plan"]["tools"]
    assert not quote["execution_plan"]["image_consent"]
    assert quote["image_estimated_percent"] is None


@pytest.mark.asyncio
async def test_expensive_auto_image_requires_signed_confirmation_and_keeps_cap(
    estimate_case, monkeypatch
):
    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_GENERATION_V2_ENABLED", True)
    session, user, conversation, request = estimate_case
    request.tool_choice, request.required_tool = "auto", None
    request.image_quality = "high"
    request.client_request_id = "expensive-auto-image"
    account = allowance_chat.allowance.account.return_value
    account.granted = 200_000
    reserve = AsyncMock()
    monkeypatch.setattr(allowance_chat.allowance, "reserve", reserve)
    with pytest.raises(HTTPException) as exc:
        await allowance_chat.admit(session, user, conversation, request)
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "usage_confirmation_required"
    reserve.assert_not_called()
    quote = exc.value.detail
    request.estimate_reference = quote["estimate_reference"]
    request.spend_limit_units = 20_000
    await allowance_chat.admit(session, user, conversation, request)
    assert reserve.call_args.kwargs["ceiling"] == 20_000
    assert reserve.call_args.kwargs["execution_plan"]["image_consent"]
    request.image_quality = "low"
    with pytest.raises(HTTPException) as exc:
        await allowance_chat.admit(session, user, conversation, request)
    assert exc.value.detail["error"] == "usage_estimate_stale"


@pytest.mark.asyncio
async def test_zero_paid_balance_does_not_authorize_auto_images(estimate_case, monkeypatch):
    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_GENERATION_V2_ENABLED", True)
    session, user, conversation, request = estimate_case
    request.tool_choice, request.required_tool = "auto", None
    account = allowance_chat.allowance.account.return_value
    account.spent = account.granted
    quote = await allowance_chat.estimate(session, user, conversation, request)
    assert "image_generation" not in quote["execution_plan"]["tools"]
    assert not quote["execution_plan"]["image_consent"]
    assert not quote["needs_confirmation"]
