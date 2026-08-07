import uuid
from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError

from app.core.work_run_settings import WorkRunDeploymentGate
from app.schemas.work_runs import CreateWorkRunRequest, WorkRunAcceptedResponse
from app.services.work_runs.contracts import (
    WorkRunKind,
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


def test_registry_exposes_only_the_first_beta_workflow() -> None:
    definitions = list_work_run_definitions()
    assert tuple(definition.kind for definition in definitions) == (
        WorkRunKind.OFFER_COMPARISON_XLSX,
    )

    definition = get_work_run_definition(WorkRunKind.OFFER_COMPARISON_XLSX)
    assert definition.version == 1
    assert definition.min_documents == 2
    assert definition.max_documents == 5
    assert definition.accepted_extensions == frozenset({".csv", ".xlsx"})
    assert "normalizing_data" in definition.stages


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
