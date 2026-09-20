from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from app.services import allowance_chat as estimates
from app.services.allowance_policy import image_budget, affordable_output, step_budget


@pytest.fixture
def estimate_case(monkeypatch):
    for key in ("R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(key, "unused-test-value")
    monkeypatch.setenv("R2_ENDPOINT", "https://storage.invalid")
    from app.api import chat_helpers

    monkeypatch.setattr(estimates.allowance, "require_enabled", lambda _: None)
    monkeypatch.setattr(
        estimates.allowance,
        "account",
        AsyncMock(
            return_value=SimpleNamespace(
                plan="start",
                granted=1250000,
                spent=0,
                reserved=0,
                luna_granted=290000,
                luna_spent=0,
                luna_reserved=0,
            )
        ),
    )
    monkeypatch.setattr(
        chat_helpers, "_build_history_for_openai", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        chat_helpers, "_resolve_system_prompt", lambda *args: "Be helpful."
    )
    session = SimpleNamespace(
        exec=AsyncMock(return_value=SimpleNamespace(all=lambda: [])), commit=AsyncMock()
    )
    user = SimpleNamespace(id="owned-user")
    conv = SimpleNamespace(
        id="owned-chat", history_summary=None, image_quality="medium"
    )
    req = SimpleNamespace(
        model="gpt-5.6-terra",
        content=[
            SimpleNamespace(
                type="text",
                value="Draw a cup.",
                model_dump=lambda: {"type": "text", "value": "Draw a cup."},
            )
        ],
        tool_choice=["image_generation"],
        required_tool="image_generation",
        reasoning_effort="low",
        thinking=True,
        image_quality="low",
        spend_limit_units=None,
        estimate_reference=None,
    )
    return session, user, conv, req


@pytest.mark.asyncio
async def test_quality_changes_estimate_and_default_quality_is_bound(estimate_case):
    s, u, c, r = estimate_case
    low = await estimates.estimate(s, u, c, r)
    r.image_quality = "high"
    high = await estimates.estimate(s, u, c, r)
    assert high["ceiling_units"] > low["ceiling_units"]
    assert high["estimated_max_percent"] > low["estimated_max_percent"]
    assert low["minimum_ceiling_units"] < 100000
    r.image_quality = None
    first = await estimates.estimate(s, u, c, r)
    r.estimate_reference = first["estimate_reference"]
    c.image_quality = "high"
    with pytest.raises(HTTPException) as error:
        await estimates.estimate(s, u, c, r)
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_automatic_expensive_image_requires_confirmation(estimate_case):
    s, u, c, r = estimate_case
    r.required_tool = None
    r.tool_choice = "auto"
    r.image_quality = "high"
    high = await estimates.estimate(s, u, c, r)
    assert high["needs_confirmation"]
    r.image_quality = "medium"
    medium = await estimates.estimate(s, u, c, r)
    assert not medium["needs_confirmation"]
    assert medium["ceiling_units"] <= 62500


@pytest.mark.asyncio
async def test_tiny_budget_rejected_before_reservation(estimate_case, monkeypatch):
    s, u, c, r = estimate_case
    r.required_tool = None
    r.tool_choice = []
    r.spend_limit_units = 1
    reserve = AsyncMock()
    monkeypatch.setattr(estimates.allowance, "reserve", reserve)
    with pytest.raises(HTTPException) as error:
        await estimates.admit(s, u, c, r)
    assert error.value.status_code == 402
    reserve.assert_not_called()


def test_output_cap_fits_remaining_budget_and_preserves_provider_maximum():
    messages = [{"role": "user", "content": [{"type": "input_text", "text": "Hello"}]}]
    budget = 12000
    maximum = affordable_output(
        "gpt-5.6-terra", messages, "Helpful", budget, target=4096
    )
    assert 256 <= maximum < 4096
    assert (
        step_budget("gpt-5.6-terra", messages, "Helpful", max_output=maximum) <= budget
    )
    assert (
        step_budget("gpt-5.6-terra", messages, "Helpful", max_output=maximum + 1)
        > budget
    )


def test_reference_images_increase_budget_and_quality_changes_output():
    assert image_budget("low") < image_budget("medium") < image_budget("high") < 100000
    assert image_budget("low", reference_tokens=1024) > image_budget("low")
