import uuid
from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError

from app.core.work_run_settings import WorkRunDeploymentGate
from app.schemas.work_runs import (
    ArtifactPreviewResponse,
    CreateWorkRunRequest,
    ReviseArtifactRequest,
    SpreadsheetWorkRunResultSummary,
    WorkRunAcceptedResponse,
    WorkRunCapabilitiesResponse,
    WorkRunListResponse,
)
from app.services.work_runs.contracts import (
    WorkRunKind,
    WorkRunOutputFeature,
    WorkRunPlanStep,
    WorkRunStatus,
    can_transition_work_run,
    get_work_run_definition,
    list_work_run_definitions,
)


def test_work_run_deployment_gate_is_fail_closed_by_default() -> None:
    gate = WorkRunDeploymentGate.from_env({})

    assert gate.master_enabled is False
    assert gate.beta_allowed_user_ids == frozenset()


def test_work_run_deployment_gate_requires_switch_and_allowlist() -> None:
    user_id = uuid.uuid4()
    gate = WorkRunDeploymentGate.from_env(
        {
            "WORK_RUNS_ENABLED": "true",
            "WORK_RUNS_BETA_ALLOWED_USER_IDS": str(user_id),
        }
    )

    assert gate.allows_beta_user(user_id) is True
    assert gate.allows_beta_user(uuid.uuid4()) is False

    disabled_gate = WorkRunDeploymentGate.from_env(
        {"WORK_RUNS_BETA_ALLOWED_USER_IDS": str(user_id)}
    )
    assert disabled_gate.allows_beta_user(user_id) is False


def test_work_run_deployment_gate_is_immutable() -> None:
    gate = WorkRunDeploymentGate.from_env({})

    with pytest.raises(FrozenInstanceError):
        gate.master_enabled = True  # type: ignore[misc]


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("WORK_RUNS_ENABLED", "sometimes"),
        ("WORK_RUNS_BETA_ALLOWED_USER_IDS", "not-a-uuid"),
    ],
)
def test_work_run_settings_reject_invalid_values(name: str, value: str) -> None:
    with pytest.raises(ValueError):
        WorkRunDeploymentGate.from_env({name: value})


def test_registry_exposes_legacy_comparison_and_spreadsheet_builder() -> None:
    definitions = list_work_run_definitions()
    kinds = {definition.kind for definition in definitions}
    assert WorkRunKind.OFFER_COMPARISON_XLSX in kinds
    assert WorkRunKind.SPREADSHEET_BUILDER_XLSX in kinds
    assert WorkRunKind.AGENTIC_TASK in kinds

    definition = get_work_run_definition(WorkRunKind.OFFER_COMPARISON_XLSX)
    assert definition.version == 2
    assert definition.min_documents == 2
    assert definition.max_documents == 5
    assert definition.accepted_extensions == frozenset({".csv", ".xlsx"})
    assert "normalizing_data" in definition.stages
    assert definition.plan_steps == (
        WorkRunPlanStep.READ_SOURCES,
        WorkRunPlanStep.ALIGN_COLUMNS,
        WorkRunPlanStep.COMBINE_ROWS,
        WorkRunPlanStep.BUILD_WORKBOOK,
        WorkRunPlanStep.VERIFY_RESULT,
    )

    builder = get_work_run_definition(WorkRunKind.SPREADSHEET_BUILDER_XLSX)
    assert builder.version == 1
    assert builder.min_documents == 1
    assert builder.max_documents == 5
    assert builder.artifact_kind == "spreadsheet_builder_xlsx"

    agent = get_work_run_definition(WorkRunKind.AGENTIC_TASK)
    assert agent.min_documents == 0
    assert agent.max_documents == 5
    assert agent.plan_steps == ()


def test_capabilities_plan_is_additive_and_defaults_for_old_callers() -> None:
    capabilities = WorkRunCapabilitiesResponse.model_validate(
        {
            "enabled": True,
            "available_kinds": ["spreadsheet_builder_xlsx"],
            "max_active_per_user": 1,
            "monthly_allowance_per_user": 25,
        }
    )

    assert capabilities.plans == []


def test_spreadsheet_result_summary_has_a_versioned_output_contract() -> None:
    summary = SpreadsheetWorkRunResultSummary(
        rows=12,
        columns=4,
        sources=2,
        normalization_mode="model",
        output_features=[
            WorkRunOutputFeature.NATIVE_EXCEL_TABLE,
            WorkRunOutputFeature.SUMMARY_SHEET,
            WorkRunOutputFeature.SOURCES_SHEET,
            WorkRunOutputFeature.INLINE_PREVIEW,
        ],
    )

    assert summary.version == 1
    assert summary.output_features[-1] == WorkRunOutputFeature.INLINE_PREVIEW


def test_create_request_normalizes_bounded_input() -> None:
    document_ids = [uuid.uuid4(), uuid.uuid4()]

    request = CreateWorkRunRequest.model_validate(
        {
            "kind": "offer_comparison_xlsx",
            "document_ids": document_ids,
            "instructions": "  Compare payment terms  ",
            "options": {
                "currency": "RUB",
            },
        }
    )

    assert request.instructions == "Compare payment terms"
    assert request.options.currency == "RUB"


def test_spreadsheet_builder_requires_goal_and_normalizes_desired_columns() -> None:
    request = CreateWorkRunRequest.model_validate(
        {
            "kind": "spreadsheet_builder_xlsx",
            "document_ids": [uuid.uuid4()],
            "instructions": "  Build a clean inventory table  ",
            "options": {
                "output_language": "en",
                "desired_columns": [" Product name ", "Stock   status"],
            },
        }
    )

    assert request.instructions == "Build a clean inventory table"
    assert request.options.desired_columns == ["Product name", "Stock status"]


@pytest.mark.parametrize("instructions", [None, "   "])
def test_spreadsheet_builder_rejects_missing_goal(instructions: str | None) -> None:
    with pytest.raises(ValidationError):
        CreateWorkRunRequest.model_validate(
            {
                "kind": "spreadsheet_builder_xlsx",
                "document_ids": [uuid.uuid4()],
                "instructions": instructions,
            }
        )


def test_spreadsheet_builder_rejects_duplicate_desired_columns() -> None:
    with pytest.raises(ValidationError):
        CreateWorkRunRequest.model_validate(
            {
                "kind": "spreadsheet_builder_xlsx",
                "document_ids": [uuid.uuid4()],
                "instructions": "Build a table",
                "options": {"desired_columns": ["Price", " price "]},
            }
        )


@pytest.mark.parametrize("column", ["=NOW()", "x" * 121])
def test_spreadsheet_builder_rejects_unsafe_desired_columns(column: str) -> None:
    with pytest.raises(ValidationError):
        CreateWorkRunRequest.model_validate(
            {
                "kind": "spreadsheet_builder_xlsx",
                "document_ids": [uuid.uuid4()],
                "instructions": "Build a table",
                "options": {"desired_columns": [column]},
            }
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"document_ids": [uuid.uuid4()]},
        {"document_ids": [uuid.uuid4()] * 2},
        {
            "document_ids": [uuid.uuid4(), uuid.uuid4()],
            "options": {"required_columns": ["Price"]},
        },
        {
            "document_ids": [uuid.uuid4(), uuid.uuid4()],
            "options": {"currency": "rub"},
        },
        {
            "document_ids": [uuid.uuid4(), uuid.uuid4()],
            "unknown": True,
        },
    ],
)
def test_create_request_rejects_unsafe_or_ambiguous_input(payload: dict) -> None:
    with pytest.raises(ValidationError):
        CreateWorkRunRequest.model_validate(
            {"kind": "offer_comparison_xlsx", **payload}
        )


def test_work_run_state_machine_allows_only_declared_transitions() -> None:
    assert can_transition_work_run(
        WorkRunStatus.ACCEPTED,
        WorkRunStatus.RESERVED,
    )
    assert can_transition_work_run(
        WorkRunStatus.RUNNING,
        WorkRunStatus.CANCELLING,
    )
    assert not can_transition_work_run(
        WorkRunStatus.VALIDATING,
        WorkRunStatus.CANCELLING,
    )
    assert not can_transition_work_run(
        WorkRunStatus.STORING,
        WorkRunStatus.CANCELLING,
    )
    assert can_transition_work_run(
        WorkRunStatus.FAILED,
        WorkRunStatus.REFUNDED,
    )
    assert not can_transition_work_run(
        WorkRunStatus.ACCEPTED,
        WorkRunStatus.SUCCEEDED,
    )
    assert not can_transition_work_run(
        WorkRunStatus.SUCCEEDED,
        WorkRunStatus.RUNNING,
    )


def test_work_run_stage_is_bounded_but_workflow_specific() -> None:
    payload = {
        "id": uuid.uuid4(),
        "status": "queued",
        "stage": "waiting_for_worker",
        "stream_url": "/api/v1/work-runs/example/stream",
    }

    response = WorkRunAcceptedResponse.model_validate(payload)
    assert response.stage == "waiting_for_worker"

    with pytest.raises(ValidationError):
        WorkRunAcceptedResponse.model_validate({**payload, "stage": "Not valid"})


def test_work_run_list_response_exposes_stable_pagination_metadata() -> None:
    response = WorkRunListResponse.model_validate(
        {"items": [], "offset": 20, "limit": 20, "has_more": True}
    )

    assert response.offset == 20
    assert response.limit == 20
    assert response.has_more is True


def test_revision_instructions_are_trimmed_and_cannot_be_blank() -> None:
    request = ReviseArtifactRequest(instructions="  Use shorter headers  ")
    assert request.instructions == "Use shorter headers"

    with pytest.raises(ValidationError):
        ReviseArtifactRequest(instructions="   ")


def test_artifact_preview_requires_a_bounded_rectangular_matrix() -> None:
    preview = ArtifactPreviewResponse.model_validate(
        {
            "version": 1,
            "goal": "Build an inventory table",
            "row_count": 2,
            "column_count": 2,
            "source_count": 1,
            "columns": [
                {"label": "Item", "data_type": "text"},
                {
                    "label": "Stock",
                    "data_type": "number",
                    "number_format": "#,##0",
                },
            ],
            "rows": [["Tea", 12], ["Coffee", 7]],
            "rows_truncated": False,
            "columns_truncated": False,
            "warning_codes": [],
        }
    )

    assert preview.rows[0] == ["Tea", 12]

    with pytest.raises(ValidationError):
        ArtifactPreviewResponse.model_validate(
            {
                **preview.model_dump(),
                "rows": [["Tea"]],
                "row_count": 1,
            }
        )
