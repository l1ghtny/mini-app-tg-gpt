import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.work_run_settings import WorkRunSettings
from app.schemas.work_runs import CreateWorkRunRequest
from app.services.work_runs.contracts import (
    WORK_RUN_DEFINITIONS,
    WorkRunKind,
    WorkRunStatus,
    can_transition_work_run,
    get_work_run_definition,
)


def test_work_run_settings_are_fail_closed_by_default() -> None:
    settings = WorkRunSettings.from_env({})

    assert settings.enabled is False
    assert settings.beta_allowed_user_ids == frozenset()
    assert settings.max_active_per_user == 1
    assert settings.monthly_allowance_per_user == 0
    assert settings.per_run_budget_usd == Decimal("0")
    assert settings.global_daily_budget_usd == Decimal("0")
    assert settings.execution_ready is False


def test_work_run_settings_require_every_execution_gate() -> None:
    user_id = uuid.uuid4()
    settings = WorkRunSettings.from_env(
        {
            "WORK_RUNS_ENABLED": "true",
            "WORK_RUNS_BETA_ALLOWED_USER_IDS": str(user_id),
            "WORK_RUNS_MAX_ACTIVE_PER_USER": "1",
            "WORK_RUNS_MONTHLY_ALLOWANCE_PER_USER": "3",
            "WORK_RUNS_PER_RUN_BUDGET_USD": "0.50",
            "WORK_RUNS_GLOBAL_DAILY_BUDGET_USD": "5.00",
        }
    )

    assert settings.execution_ready is True
    assert settings.allows_user(user_id) is True
    assert settings.allows_user(uuid.uuid4()) is False


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("WORK_RUNS_BETA_ALLOWED_USER_IDS", "not-a-uuid"),
        ("WORK_RUNS_MAX_ACTIVE_PER_USER", "-1"),
        ("WORK_RUNS_MONTHLY_ALLOWANCE_PER_USER", "many"),
        ("WORK_RUNS_PER_RUN_BUDGET_USD", "NaN"),
        ("WORK_RUNS_GLOBAL_DAILY_BUDGET_USD", "-0.1"),
    ],
)
def test_work_run_settings_reject_invalid_values(name: str, value: str) -> None:
    with pytest.raises(ValueError):
        WorkRunSettings.from_env({name: value})


def test_registry_exposes_only_the_first_beta_workflow() -> None:
    assert set(WORK_RUN_DEFINITIONS) == {WorkRunKind.OFFER_COMPARISON_XLSX}

    definition = get_work_run_definition(WorkRunKind.OFFER_COMPARISON_XLSX)
    assert definition.version == 1
    assert definition.min_documents == 2
    assert definition.max_documents == 5
    assert definition.accepted_extensions == frozenset({".csv", ".xlsx"})
    assert definition.reserved_units == 1


def test_create_request_normalizes_bounded_input() -> None:
    document_ids = [uuid.uuid4(), uuid.uuid4()]

    request = CreateWorkRunRequest.model_validate(
        {
            "kind": "offer_comparison_xlsx",
            "document_ids": document_ids,
            "instructions": "  Compare payment terms  ",
            "options": {
                "currency": "RUB",
                "required_columns": [" Price ", "Payment terms"],
            },
        }
    )

    assert request.instructions == "Compare payment terms"
    assert request.options.required_columns == ["Price", "Payment terms"]


@pytest.mark.parametrize(
    "payload",
    [
        {"document_ids": [uuid.uuid4()]},
        {"document_ids": [uuid.uuid4()] * 2},
        {
            "document_ids": [uuid.uuid4(), uuid.uuid4()],
            "options": {"required_columns": ["Price", "price"]},
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
