from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("R2_BUCKET", "test-public-bucket")
os.environ.setdefault("R2_ENDPOINT", "https://example.r2.cloudflarestorage.com")
os.environ.setdefault("R2_ACCESS_KEY_ID", "test-access-key")
os.environ.setdefault("R2_SECRET_ACCESS_KEY", "test-secret-key")

from app.db.work_agent_models import WorkHumanInputRequest
from app.services.work_runs import agent_execution
from app.services.work_runs.contracts import (
    WorkRunStatus,
    can_transition_work_run,
)
from app.services.work_runs.human_input import (
    ASK_USER_TOOL,
    parse_ask_user_call,
)


def _tool_response(
    *,
    question: str = "Which market should this recommendation target?",
    reason: str = "The market changes the applicable evidence and recommendation.",
) -> SimpleNamespace:
    return SimpleNamespace(
        id="resp_ask_1",
        output_text="",
        output=[
            SimpleNamespace(
                type="function_call",
                name="ask_user",
                call_id="call_ask_1",
                arguments=json.dumps({"question": question, "reason": reason}),
            )
        ],
    )


def test_ask_user_tool_uses_a_strict_schema() -> None:
    parameters = ASK_USER_TOOL["parameters"]

    assert ASK_USER_TOOL["strict"] is True
    assert parameters["required"] == ["question", "reason"]
    assert parameters["additionalProperties"] is False


def test_parse_ask_user_call_preserves_provider_continuation_ids() -> None:
    call = parse_ask_user_call(_tool_response())

    assert call is not None
    assert call.provider_response_id == "resp_ask_1"
    assert call.provider_call_id == "call_ask_1"
    assert call.question == "Which market should this recommendation target?"


@pytest.mark.parametrize(
    "question",
    [
        "What is your API key?",
        "Пришлите пароль от аккаунта.",
        "Please paste the access token.",
    ],
)
def test_parse_ask_user_call_rejects_secret_requests(question: str) -> None:
    with pytest.raises(ValueError, match="cannot request secrets"):
        parse_ask_user_call(_tool_response(question=question))


@pytest.mark.asyncio
async def test_resume_returns_the_answer_to_the_original_tool_call() -> None:
    create = AsyncMock(return_value=SimpleNamespace(id="resp_resumed"))
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    request = WorkHumanInputRequest(
        id=uuid.uuid4(),
        thread_id=uuid.uuid4(),
        work_run_id=uuid.uuid4(),
        round=1,
        status="answered",
        question="Which market?",
        reason="The evidence differs.",
        answer="Slovakia and Czechia.",
        provider="openai",
        provider_response_id="resp_ask_1",
        provider_call_id="call_ask_1",
        created_at=datetime(2026, 8, 15, 12, 0, 0),
    )

    await agent_execution._generate_draft(
        client=client,  # type: ignore[arg-type]
        request_payload={},
        tools=[ASK_USER_TOOL],
        revision_feedback=None,
        resume_request=request,
    )

    kwargs = create.await_args.kwargs
    assert kwargs["previous_response_id"] == "resp_ask_1"
    assert kwargs["input"] == [
        {
            "type": "function_call_output",
            "call_id": "call_ask_1",
            "output": "Slovakia and Czechia.",
        }
    ]


@pytest.mark.asyncio
async def test_validated_generation_pauses_before_reviewing_a_question() -> None:
    response = _tool_response()
    client = SimpleNamespace(
        responses=SimpleNamespace(create=AsyncMock(return_value=response))
    )
    handler = AsyncMock(return_value=True)
    observer = AsyncMock()

    with pytest.raises(agent_execution.WorkRunAwaitingUser):
        await agent_execution._generate_validated_result(
            client=client,  # type: ignore[arg-type]
            request_payload={},
            tools=[ASK_USER_TOOL],
            observe_response=observer,
            handle_human_input=handler,
        )

    observer.assert_awaited_once_with("draft_1", response)
    handler.assert_awaited_once_with(response)
    assert client.responses.create.await_count == 1


@pytest.mark.asyncio
async def test_resumed_clarification_is_available_to_review_and_retry() -> None:
    request = WorkHumanInputRequest(
        id=uuid.uuid4(),
        thread_id=uuid.uuid4(),
        work_run_id=uuid.uuid4(),
        round=1,
        status="answered",
        question="What is the product, audience, and channel?",
        reason="Those details materially change the announcement.",
        answer=(
            "Atlas is a private beta for operations leads at design partners, sent "
            "by email with a call to action to book onboarding."
        ),
        provider="openai",
        provider_response_id="resp_ask_1",
        provider_call_id="call_ask_1",
        created_at=datetime(2026, 9, 1, 12, 0, 0),
    )
    resumed_draft = SimpleNamespace(
        id="draft-resumed",
        output_text=(
            "Subject: Atlas private beta\n\nOperations leads can now book onboarding "
            "for the Atlas private beta. Ready to choose a time?"
        ),
        output=[],
    )
    failed_review = SimpleNamespace(
        id="review-failed",
        output_text=json.dumps(
            {
                "passes": False,
                "issues": ["Make the call to action more direct."],
                "revision_instructions": "End with a direct booking call to action.",
            }
        ),
        output=[],
    )
    corrected_draft = SimpleNamespace(
        id="draft-corrected",
        output_text=(
            "Subject: Book onboarding for the Atlas private beta\n\nAtlas is now "
            "available to operations leads at our design partners. Book your "
            "onboarding session to get started."
        ),
        output=[],
    )
    passed_review = SimpleNamespace(
        id="review-passed",
        output_text=json.dumps(
            {"passes": True, "issues": [], "revision_instructions": ""}
        ),
        output=[],
    )
    create = AsyncMock(
        side_effect=[resumed_draft, failed_review, corrected_draft, passed_review]
    )
    client = SimpleNamespace(responses=SimpleNamespace(create=create))

    result = await agent_execution._generate_validated_result(
        client=client,  # type: ignore[arg-type]
        request_payload={
            "current_request": (
                "Prepare a launch announcement. Ask one concise material question "
                "before writing the announcement."
            ),
            "approved_plan": {"expected_outputs": []},
        },
        tools=[ASK_USER_TOOL],
        observe_response=AsyncMock(),
        resume_request=request,
        handle_human_input=AsyncMock(return_value=False),
    )

    assert result.content == corrected_draft.output_text
    first_review_payload = json.loads(
        create.await_args_list[1].kwargs["input"][1]["content"][0]["text"]
    )
    retry_payload = json.loads(
        create.await_args_list[2].kwargs["input"][1]["content"][0]["text"]
    )
    for payload in (first_review_payload, retry_payload):
        assert payload["answered_clarification"] == {
            "question": request.question,
            "answer": request.answer,
        }
    assert "previous_response_id" not in create.await_args_list[2].kwargs


@pytest.mark.asyncio
async def test_one_question_request_cannot_pause_for_second_clarification() -> None:
    request = WorkHumanInputRequest(
        id=uuid.uuid4(),
        thread_id=uuid.uuid4(),
        work_run_id=uuid.uuid4(),
        round=1,
        status="answered",
        question="What are the product name, audience, and delivery channel?",
        reason="Those details materially change the announcement.",
        answer=(
            "Atlas private beta for operations leads at design partners, sent by "
            "email with a call to action to book onboarding."
        ),
        provider="openai",
        provider_response_id="resp_ask_1",
        provider_call_id="call_ask_1",
        created_at=datetime(2026, 9, 1, 12, 0, 0),
    )
    second_question = _tool_response(
        question="What primary benefit does Atlas provide?",
        reason="The product benefit would make the announcement more specific.",
    )
    completed_draft = SimpleNamespace(
        id="draft-complete",
        output_text=(
            "Subject: Book onboarding for the Atlas private beta\n\n"
            "Atlas is now available to operations leads at our design partners. "
            "Assumption: Atlas helps teams streamline day-to-day operations. "
            "Book your onboarding session to get started."
        ),
        output=[],
    )
    passed_review = SimpleNamespace(
        id="review-passed",
        output_text=json.dumps(
            {"passes": True, "issues": [], "revision_instructions": ""}
        ),
        output=[],
    )
    create = AsyncMock(side_effect=[second_question, completed_draft, passed_review])
    handler = AsyncMock(return_value=True)
    client = SimpleNamespace(responses=SimpleNamespace(create=create))

    result = await agent_execution._generate_validated_result(
        client=client,  # type: ignore[arg-type]
        request_payload={
            "current_request": (
                "Prepare a launch announcement. Ask one concise material question "
                "before writing the announcement."
            ),
            "approved_plan": {"expected_outputs": []},
        },
        tools=[ASK_USER_TOOL],
        observe_response=AsyncMock(),
        resume_request=request,
        handle_human_input=handler,
    )

    assert result.content == completed_draft.output_text
    handler.assert_not_awaited()
    for call in create.await_args_list[:2]:
        assert not agent_execution._ask_user_available(call.kwargs["tools"])
    retry_payload = json.loads(
        create.await_args_list[1].kwargs["input"][1]["content"][0]["text"]
    )
    assert retry_payload["answered_clarification"] == {
        "question": request.question,
        "answer": request.answer,
    }


def test_waiting_run_can_resume_or_stop_but_not_finish_directly() -> None:
    assert can_transition_work_run(
        WorkRunStatus.RUNNING,
        WorkRunStatus.WAITING_FOR_USER,
    )
    assert can_transition_work_run(
        WorkRunStatus.WAITING_FOR_USER,
        WorkRunStatus.QUEUED,
    )
    assert can_transition_work_run(
        WorkRunStatus.WAITING_FOR_USER,
        WorkRunStatus.CANCELLED,
    )
    assert not can_transition_work_run(
        WorkRunStatus.WAITING_FOR_USER,
        WorkRunStatus.SUCCEEDED,
    )


def test_human_input_table_enforces_rounds_statuses_and_one_pending_question() -> None:
    constraint_names = {
        constraint.name
        for constraint in WorkHumanInputRequest.__table__.constraints
    }
    index_names = {index.name for index in WorkHumanInputRequest.__table__.indexes}

    assert "ck_work_human_input_round" in constraint_names
    assert "ck_work_human_input_status" in constraint_names
    assert "uq_work_human_input_pending_run" in index_names
