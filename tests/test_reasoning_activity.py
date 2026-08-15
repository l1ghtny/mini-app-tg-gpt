from types import SimpleNamespace

from app.services.reasoning_activity import ReasoningActivity, extract_summary_text


def test_extract_summary_text_handles_google_step_start_blocks():
    blocks = [
        SimpleNamespace(type="text", text="Checking the request. "),
        {"type": "text", "text": "Choosing a concise answer."},
    ]

    assert extract_summary_text(blocks) == (
        "Checking the request. Choosing a concise answer."
    )


def test_extract_summary_text_falls_back_from_empty_text_to_content():
    block = SimpleNamespace(text=None, content="Available summary")

    assert extract_summary_text(block) == "Available summary"


def test_empty_summary_emits_lifecycle_without_fabricating_detail():
    activity = ReasoningActivity(provider="google")

    started = activity.start(segment_id="thought-0", source_event="step.start")
    completed = activity.complete(segment_id="thought-0", source_event="step.stop")

    assert [event["status"] for event in started + completed] == ["active", "done"]
    assert not any(event["type"] == "reasoning.summary.done" for event in completed)
    assert activity.text == ""


def test_multiple_openai_summary_sections_complete_with_the_full_activity():
    activity = ReasoningActivity(provider="openai")

    activity.append(
        "**Checking constraints**\n\nReviewed the request.",
        segment_id="rs-1:0",
        source_event="response.reasoning_summary_text.delta",
    )
    first_done = activity.complete(
        segment_id="rs-1:0",
        source_event="response.reasoning_summary_text.done",
        full_text="**Checking constraints**\n\nReviewed the request.",
    )
    activity.append(
        "**Preparing the answer**\n\nSelected the clearest structure.",
        segment_id="rs-1:1",
        source_event="response.reasoning_summary_text.delta",
    )
    second_done = activity.complete(
        segment_id="rs-1:1",
        source_event="response.reasoning_summary_text.done",
        full_text="**Preparing the answer**\n\nSelected the clearest structure.",
    )

    assert first_done[-2]["text"].startswith("**Checking constraints**")
    assert second_done[-2]["text"] == activity.text
    assert "\n\n**Preparing the answer**" in activity.text


def test_activity_is_bounded_and_marks_truncation():
    activity = ReasoningActivity(provider="google", max_chars=8)

    events = activity.append(
        "1234567890",
        segment_id="thought-0",
        source_event="step.delta",
    )
    completed = activity.complete(
        segment_id="thought-0",
        source_event="step.stop",
    )

    assert events[-1]["delta"] == "12345678"
    assert activity.text == "12345678"
    assert completed[-2]["truncated"] is True


def test_done_only_summary_is_bounded():
    activity = ReasoningActivity(provider="openai", max_chars=8)

    completed = activity.complete(
        segment_id="rs-1:0",
        source_event="done",
        full_text="1234567890",
    )

    assert activity.text == "12345678"
    assert completed[-2]["text"] == "12345678"
    assert completed[-2]["truncated"] is True


def test_new_segment_reactivates_reasoning_after_previous_completion():
    activity = ReasoningActivity(provider="google")
    activity.append("First", segment_id="thought-0", source_event="step.delta")
    activity.complete(segment_id="thought-0", source_event="step.stop")

    events = activity.append("Second", segment_id="thought-1", source_event="step.delta")

    assert events[0]["type"] == "status"
    assert events[0]["status"] == "active"
    assert events[1]["type"] == "reasoning.summary.delta"


def test_completed_segment_ignores_replayed_provider_events():
    activity = ReasoningActivity(provider="openai")
    activity.append("Stable", segment_id="rs-1:0", source_event="delta")
    activity.complete(segment_id="rs-1:0", source_event="done", full_text="Stable")

    replay_delta = activity.append(
        "Stable",
        segment_id="rs-1:0",
        source_event="delta",
    )
    replay_done = activity.complete(
        segment_id="rs-1:0",
        source_event="done",
        full_text="A replay must not replace the completed summary",
    )

    assert replay_delta == []
    assert replay_done == []
    assert activity.text == "Stable"
