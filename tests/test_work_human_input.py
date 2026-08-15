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
