from __future__ import annotations

import json
import uuid
from datetime import datetime
from importlib import import_module
from io import StringIO
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from pydantic import ValidationError

from app.db.work_agent_models import WorkThreadMessage
from app.schemas.work_runs import CreateWorkRunRequest
from app.schemas.work_threads import (
    CreateWorkFollowUpRequest,
    CreateWorkThreadRequest,
    SendWorkMessageRequest,
    UpdateWorkPlanRequest,
)
from app.services.work_threads.history import bounded_thread_history
from app.services.work_threads.planner import WorkPlanningError, plan_work
from app.services.work_threads import service as thread_service
from app.services.work_runs.contracts import WorkRunKind


@pytest.fixture
def rebuild_test_db() -> None:
    """These contract and service-isolation tests do not use the database."""


def test_work_thread_goal_does_not_require_a_preselected_workflow() -> None:
    request = CreateWorkThreadRequest.model_validate(
        {
            "goal": "Research this market and recommend a launch position",
            "document_ids": [],
            "output_language": "en",
        }
    )

    assert request.goal.startswith("Research")
    assert request.document_ids == []


def test_work_thread_uses_the_current_request_language_by_default() -> None:
    request = CreateWorkThreadRequest.model_validate(
        {"goal": "Vergleiche diese Angebote und empfehle eines."}
    )

    assert request.output_language == "auto"


def test_stored_thread_language_does_not_override_the_current_turn() -> None:
    legacy_thread = SimpleNamespace(context_manifest={"output_language": "ru"})
    explicit_thread = SimpleNamespace(
        context_manifest={
            "output_language": "ru",
            "output_language_source": "explicit",
        }
    )

    assert thread_service._thread_output_language(legacy_thread) == "auto"
    assert thread_service._thread_output_language(explicit_thread) == "auto"


def test_automatic_language_is_scoped_to_the_general_agent() -> None:
    agent_request = CreateWorkRunRequest.model_validate(
        {
            "kind": WorkRunKind.AGENTIC_TASK,
            "document_ids": [],
            "instructions": "Analyse the request.",
            "options": {"output_language": "auto"},
        }
    )

    assert agent_request.options.output_language == "auto"
    with pytest.raises(ValidationError, match="only supported for agentic work"):
        CreateWorkRunRequest.model_validate(
            {
                "kind": WorkRunKind.SPREADSHEET_BUILDER_XLSX,
                "document_ids": [uuid.uuid4()],
                "instructions": "Build a workbook.",
                "options": {"output_language": "auto"},
            }
        )


@pytest.mark.asyncio
async def test_thread_response_exposes_document_names_in_context_order() -> None:
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    now = datetime.now()
    thread = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        title="Supplier comparison",
        goal="Compare the attached offers",
        status="completed",
        conversation_id=None,
        folder_id=None,
        created_at=now,
        updated_at=now,
        context_manifest={"document_ids": [str(first_id), str(second_id)]},
    )
    documents = [
        SimpleNamespace(
            id=second_id,
            filename="offer-b.csv",
            mime_type="text/csv",
            status="ready",
        ),
        SimpleNamespace(
            id=first_id,
            filename="offer-a.csv",
            mime_type="text/csv",
            status="ready",
        ),
    ]
    session = SimpleNamespace(
        exec=AsyncMock(
            side_effect=[
                SimpleNamespace(all=lambda: []),
                SimpleNamespace(first=lambda: None),
                SimpleNamespace(all=lambda: []),
                SimpleNamespace(all=lambda: []),
                SimpleNamespace(all=lambda: documents),
            ]
        ),
    )

    response = await thread_service.thread_response(session, thread)

    assert [document.filename for document in response.documents] == [
        "offer-a.csv",
        "offer-b.csv",
    ]


def test_edited_plan_requires_distinct_stable_step_ids() -> None:
    payload = {
        "title": "Decision brief",
        "summary": "Review the evidence and recommend an option.",
        "steps": [
            {"id": "review", "title": "Review", "description": "Read the sources."},
            {"id": "review", "title": "Decide", "description": "Compare the options."},
        ],
        "expected_outputs": [
            {"kind": "answer", "label": "Brief", "description": "A recommendation."}
        ],
    }

    with pytest.raises(ValueError, match="step ids must be unique"):
        UpdateWorkPlanRequest.model_validate(payload)


def test_follow_up_contract_normalizes_instruction_and_limits_intent() -> None:
    request = CreateWorkFollowUpRequest.model_validate(
        {"instruction": "  Make   the recommendation shorter. ", "intent": "revise"}
    )

    assert request.instruction == "Make the recommendation shorter."
    assert request.intent == "revise"


def test_conversation_message_accepts_unique_file_context() -> None:
    document_id = uuid.uuid4()
    request = SendWorkMessageRequest.model_validate(
        {
            "content": "  Analyse   this website and explain the risks. ",
            "document_ids": [str(document_id)],
        }
    )

    assert request.content == "Analyse this website and explain the risks."
    assert request.document_ids == [document_id]


def test_conversation_message_can_steer_an_active_run() -> None:
    request = SendWorkMessageRequest.model_validate(
        {
            "content": "Focus only on the pricing risks.",
            "steer_active": True,
        }
    )

    assert request.steer_active is True
    assert request.document_ids == []


@pytest.mark.asyncio
async def test_active_steering_is_persisted_for_the_current_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_id = uuid.uuid4()
    run_id = uuid.uuid4()
    thread = SimpleNamespace(id=thread_id, status="running", updated_at=None)
    active_run = SimpleNamespace(id=run_id, status="running", options={})
    session = SimpleNamespace(
        exec=AsyncMock(
            side_effect=[
                SimpleNamespace(all=lambda: []),
                SimpleNamespace(first=lambda: SimpleNamespace(work_run_id=run_id)),
            ]
        ),
        get=AsyncMock(return_value=active_run),
        add=MagicMock(),
        commit=AsyncMock(),
    )
    monkeypatch.setattr(
        thread_service,
        "_existing_execution",
        AsyncMock(return_value=None),
    )

    returned_thread, returned_run = await thread_service.send_message(
        session=session,  # type: ignore[arg-type]
        user=SimpleNamespace(id=uuid.uuid4()),
        thread=thread,  # type: ignore[arg-type]
        request=SendWorkMessageRequest(
            content="Focus only on the pricing risks.",
            steer_active=True,
        ),
        client_request_id="steering-request-1",
    )

    assert returned_thread is thread
    assert returned_run is None
    assert active_run.options["steering_pending"] is True
    steering_message = next(
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], WorkThreadMessage)
    )
    assert steering_message.content == "Focus only on the pricing risks."
    assert steering_message.message_metadata == {
        "client_request_id": "steering-request-1",
        "steering_for_run_id": str(run_id),
        "steering_applied": False,
    }
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_retry_starts_a_ready_thread_that_never_received_a_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread = SimpleNamespace(id=uuid.uuid4(), status="ready", context_manifest={})
    plan = SimpleNamespace(id=uuid.uuid4(), version=2, status="proposed")
    run = SimpleNamespace(id=uuid.uuid4(), status="accepted")
    approve = AsyncMock(return_value=(plan, run))
    monkeypatch.setattr(
        thread_service,
        "_existing_execution",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(thread_service, "_latest_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr(thread_service, "approve_plan", approve)

    returned_thread, returned_run = await thread_service.retry_conversation_turn(
        session=SimpleNamespace(),  # type: ignore[arg-type]
        user=SimpleNamespace(id=uuid.uuid4()),
        thread=thread,  # type: ignore[arg-type]
        client_request_id="retry-ready-thread-1",
    )

    assert returned_thread is thread
    assert returned_run is run
    approve.assert_awaited_once_with(
        session=ANY,
        user=ANY,
        thread=thread,
        plan_version=2,
        client_request_id="retry-ready-thread-1",
    )


def test_agent_history_keeps_previous_results_but_not_current_request() -> None:
    messages = [
        WorkThreadMessage(thread_id=uuid.uuid4(), role="assistant", kind="result", content="First result"),
        WorkThreadMessage(thread_id=uuid.uuid4(), role="user", kind="follow_up", content="Make it shorter"),
    ]

    history = bounded_thread_history(messages, current_request="Make it shorter")

    assert [item["content"] for item in history] == ["First result"]


@pytest.mark.asyncio
async def test_conversation_planner_uses_the_general_agent() -> None:
    response = SimpleNamespace(
        id="resp-plan-1",
        output_text=json.dumps(
            {
                "title": "Supplier decision",
                "summary": "Compare the evidence and recommend the strongest option.",
                "execution_kind": "agentic_task",
                "steps": [
                    {"id": "review", "title": "Review", "description": "Read the supplied evidence."},
                    {"id": "decide", "title": "Decide", "description": "Compare trade-offs and recommend an option."},
                ],
                "expected_outputs": [
                    {
                        "kind": "answer",
                        "label": "Decision brief",
                        "description": "A readable recommendation.",
                        "acceptance_criteria": [
                            "Names the recommended supplier and explains the decisive evidence."
                        ],
                    }
                ],
                "assumptions": [],
            }
        ),
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=80,
            input_tokens_details=SimpleNamespace(cached_tokens=0),
            output_tokens_details=SimpleNamespace(reasoning_tokens=10),
        ),
    )
    create = AsyncMock(return_value=response)
    client = SimpleNamespace(responses=SimpleNamespace(create=create))

    result = await plan_work(
        goal="Tell me which supplier to choose",
        documents=[{"filename": "brief.pdf", "mime_type": "application/pdf"}],
        output_language="en",
        context={"previous_result": "Supplier A is stronger."},
        client=client,
    )

    assert result.plan.execution_kind == "agentic_task"
    assert result.plan.steps[0].id == "review"
    assert result.usage["reasoning_tokens"] == 10
    request = create.await_args.kwargs
    assert request["text"]["format"]["type"] == "json_schema"
    plan_schema = request["text"]["format"]["schema"]
    assert plan_schema["properties"]["execution_kind"]["const"] == "agentic_task"
    assert "assumptions" in request["text"]["format"]["schema"]["required"]
    output_schema = plan_schema["$defs"]["PlannedOutput"]
    assert output_schema["properties"]["kind"]["enum"] == ["answer", "artifact"]
    assert "acceptance_criteria" in output_schema["required"]
    assert result.plan.expected_outputs[0].acceptance_criteria == [
        "Names the recommended supplier and explains the decisive evidence."
    ]
    user_payload = json.loads(request["input"][1]["content"][0]["text"])
    assert user_payload["context"]["previous_result"] == "Supplier A is stronger."
    developer_prompt = request["input"][0]["content"][0]["text"]
    assert "Attached file types are source context" in developer_prompt
    assert "saved thread metadata" in developer_prompt


@pytest.mark.asyncio
async def test_conversation_planner_rejects_the_specialized_spreadsheet_executor():
    response = SimpleNamespace(
        id="resp-plan-specialized",
        output_text=json.dumps(
            {
                "title": "Supplier decision",
                "summary": "Compare the attached supplier offers.",
                "execution_kind": "spreadsheet_builder_xlsx",
                "steps": [
                    {
                        "id": "review",
                        "title": "Review",
                        "description": "Read both files.",
                    },
                    {
                        "id": "decide",
                        "title": "Decide",
                        "description": "Recommend one supplier.",
                    },
                ],
                "expected_outputs": [
                    {
                        "kind": "spreadsheet",
                        "label": "Comparison",
                        "description": "A supplier comparison.",
                        "acceptance_criteria": ["Recommends one supplier."],
                    }
                ],
                "assumptions": [],
            }
        ),
        usage=None,
    )
    client = SimpleNamespace(
        responses=SimpleNamespace(create=AsyncMock(return_value=response))
    )

    with pytest.raises(WorkPlanningError, match="invalid plan"):
        await plan_work(
            goal="Compare these CSV files and recommend a supplier.",
            documents=[{"filename": "offer.csv", "mime_type": "text/csv"}],
            output_language="auto",
            client=client,
        )


def test_agentic_work_migration_is_additive_and_uses_committed_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = import_module(
        "migrations.versions.xe2f3a4b5c6d_add_agentic_work_threads"
    )
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    monkeypatch.setattr(migration, "op", Operations(context))

    migration.upgrade()

    sql = output.getvalue().lower()
    assert migration.down_revision == "vc1d2e3f4a5b"
    for table in (
        "work_thread",
        "work_thread_message",
        "work_plan",
        "work_thread_run",
    ):
        assert f"create table {table}" in sql
    assert "'agentic_task', true, 1, 25, 1.000000, 10.000000, 2" in " ".join(
        sql.split()
    )
    assert "drop table" not in sql
    assert "drop column" not in sql
