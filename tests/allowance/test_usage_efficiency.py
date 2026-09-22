"""Regressions for repeated vision input, consistent quotes and bounded recall."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import allowance_context as context
from app.services import shared_chat_provider as provider
from app.services.allowance_policy import input_upper_bound, step_budget
from app.services import allowance_chat as estimates


def msg(text, role="user", identifier=None, image=None):
    parts = [{"type": "input_text", "text": text}]
    if image:
        parts.append({"type": "input_image", "image_url": image})
    return {"role": role, "content": parts, "_message_id": identifier}


def pixels(messages):
    return [p for m in messages for p in m["content"] if p.get("type") == "input_image"]


def image_history():
    result = []
    for i in range(19):
        result.extend(
            [
                msg(
                    f"Check entry {i}",
                    identifier=f"user-{i}",
                    image=f"https://owned.invalid/{i}.png",
                ),
                msg(
                    f"Entry {i} contains 42 grams protein, no nuts.",
                    "assistant",
                    f"assistant-{i}",
                ),
            ]
        )
    return result


def test_old_images_are_references_and_new_image_keeps_visual_input():
    source = image_history() + [
        msg("Compare with this", image="https://owned.invalid/new.png")
    ]
    original = deepcopy(source)
    prepared, refs = context.prepare_visual_history(source)
    assert len(pixels(prepared)) == 1
    assert pixels(prepared)[0]["detail"] == "high"
    assert len(refs) == 20
    assert "42 grams protein" in str(prepared)
    assert "image_" in str(prepared)
    assert source == original  # Persistence and image-edit sources remain untouched.


def test_followup_removes_all_old_pixels_and_reduces_input_cost_by_over_90_percent():
    history = image_history() + [msg("What is the total protein?")]
    prepared, _, refs = context.estimate_context(history)
    assert not pixels(prepared)
    assert len(refs) == 19
    assert (
        step_budget("gpt-5.6-sol", prepared, "", max_output=1200)
        < step_budget("gpt-5.6-sol", history, "", max_output=1200) / 10
    )


def test_image_weight_is_part_of_history_partition():
    old, recent = context.context_partition(
        [msg("", image="owned"), msg("follow-up")], 1000
    )
    assert len(old) == 1
    assert len(recent) == 1


def test_summary_boundary_remains_stable_between_compactions(monkeypatch):
    monkeypatch.setattr(context.settings, "SHARED_ALLOWANCE_HISTORY_TOKENS", 100)
    history = [
        msg("old facts " * 100, identifier="old"),
        msg("recent", identifier="new"),
    ]
    conv = SimpleNamespace(
        history_summary="The number is 42.", history_summary_up_to_message_id="old"
    )
    older, recent, summary, _ = context.context_plan(history, conv)
    assert older == []
    assert len(recent) == 1
    assert summary == "The number is 42."
    history += [msg("Another short follow-up", identifier="next")]
    assert context.context_plan(history, conv)[0] == []


@pytest.mark.parametrize("granted", [1250000, 2500000, 6250000, 25000000])
@pytest.mark.asyncio
async def test_image_history_does_not_trigger_half_allowance_quotes(
    estimate_case, monkeypatch, granted
):
    from app.api import chat_helpers

    s, u, c, r = estimate_case
    account = await estimates.allowance.account(s, u.id)
    account.granted = granted
    account.plan = "premium"
    r.model = "gpt-5.6-sol"
    r.tool_choice = "auto"
    r.required_tool = None
    r.image_quality = "medium"
    monkeypatch.setattr(
        chat_helpers,
        "_build_history_for_openai",
        AsyncMock(return_value=image_history()),
    )
    quote = await estimates.estimate(s, u, c, r)
    assert not quote["needs_confirmation"]
    assert quote["ceiling_percent"] <= 5
    assert quote["estimated_max_percent"] <= quote["ceiling_percent"]
    assert quote["minimum_ceiling_units"] <= quote["ceiling_units"]


@pytest.mark.asyncio
async def test_estimate_includes_current_image_and_current_prompt(estimate_case):
    s, u, c, r = estimate_case
    r.required_tool = None
    r.tool_choice = []
    before = await estimates.estimate(s, u, c, r)
    r.content.append(
        SimpleNamespace(
            type="image_url",
            value="https://owned.invalid/current",
            model_dump=lambda: {
                "type": "image_url",
                "value": "https://owned.invalid/current",
            },
        )
    )
    after = await estimates.estimate(s, u, c, r)
    assert after["estimated_min_percent"] > before["estimated_min_percent"]
    assert after["minimum_ceiling_units"] > before["minimum_ceiling_units"]


@pytest.mark.asyncio
async def test_unknown_image_id_cannot_fetch_or_spend(monkeypatch):
    from app.services import allowance_images

    fetch = AsyncMock()
    monkeypatch.setattr(allowance_images, "image_files", fetch)
    run = SimpleNamespace(image_references={}, response=AsyncMock())
    with pytest.raises(ValueError, match="Invalid image references"):
        _ = [
            e
            async for e in provider.run_tool(
                run,
                "inspect_image",
                {"query": "Read it", "image_ids": ["foreign"], "detail": "original"},
                {"inspect_image": {}},
                [],
                0,
            )
        ]
    fetch.assert_not_called()
    run.response.assert_not_called()


@pytest.mark.asyncio
async def test_targeted_inspection_fetches_only_requested_image_and_reuses_result(
    monkeypatch,
):
    from app.services import allowance_images

    fetch = AsyncMock(return_value=[("source.png", b"image", "image/png")])
    monkeypatch.setattr(allowance_images, "image_files", fetch)
    response = AsyncMock(return_value=(SimpleNamespace(output_text="42 grams"), []))
    run = SimpleNamespace(
        image_references={
            "first": {"image_url": "owned-1"},
            "second": {"image_url": "owned-2"},
        },
        inspected_images={},
        response=response,
    )
    args = {"query": "Read the amount", "image_ids": ["second"], "detail": "original"}
    for _ in range(2):
        events = [
            e
            async for e in provider.run_tool(
                run, "inspect_image", args, {"inspect_image": {}}, [], 0
            )
        ]
        assert events[-1]["result"] == "42 grams"
    fetch.assert_awaited_once_with([{"image_url": "owned-2"}], run)
    response.assert_awaited_once()
    call = response.call_args.kwargs
    assert call["model"] == "gpt-5.6-luna"
    assert pixels(call["messages"])[0]["detail"] == "original"


def test_binary_image_payload_is_not_counted_as_text_tokens():
    short = [
        {
            "role": "user",
            "content": [
                {
                    "type": "input_image",
                    "image_url": "data:image/png;base64,abc",
                    "detail": "high",
                }
            ],
        }
    ]
    large = deepcopy(short)
    large[0]["content"][0]["image_url"] += "x" * 1_000_000
    assert input_upper_bound(short) == input_upper_bound(large)


def test_legacy_summary_keeps_old_images_addressable():
    history = image_history() + [msg("Follow-up")]
    conv = SimpleNamespace(
        history_summary="Protein observations.",
        history_summary_up_to_message_id="assistant-18",
    )
    messages, _, refs = context.estimate_context(history, conv)
    assert not pixels(messages)
    assert all(ref in str(messages) for ref in refs)


def test_claude_quote_and_native_payload_use_same_image_budget():
    messages = [msg("Read", image="owned")]
    messages, _ = context.prepare_visual_history(messages)
    native = provider.claude_messages(messages)
    # Serialization overhead may differ slightly; vision reserve must not jump 5x.
    for model in ["claude-sonnet-5", "claude-opus-5", "claude-fable-5-1"]:
        quoted = step_budget(model, messages, "", max_output=256)
        actual = step_budget(model, native, "", max_output=256)
        assert abs(quoted - actual) < 500
