from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class WorkRunKind(StrEnum):
    OFFER_COMPARISON_XLSX = "offer_comparison_xlsx"
    SPREADSHEET_BUILDER_XLSX = "spreadsheet_builder_xlsx"


class WorkRunStatus(StrEnum):
    ACCEPTED = "accepted"
    RESERVED = "reserved"
    QUEUED = "queued"
    RUNNING = "running"
    VALIDATING = "validating"
    STORING = "storing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class WorkRunErrorCode(StrEnum):
    DISABLED = "work_runs_disabled"
    USER_NOT_ALLOWED = "work_run_user_not_allowed"
    KIND_NOT_SUPPORTED = "work_run_kind_not_supported"
    INVALID_INPUT = "work_run_invalid_input"
    DOCUMENTS_NOT_READY = "work_run_documents_not_ready"
    ENTITLEMENT_DENIED = "work_run_entitlement_denied"
    ACTIVE_LIMIT_REACHED = "work_run_active_limit_reached"
    MONTHLY_ALLOWANCE_EXHAUSTED = "work_run_monthly_allowance_exhausted"
    PER_RUN_BUDGET_EXCEEDED = "work_run_per_run_budget_exceeded"
    DAILY_BUDGET_EXCEEDED = "work_run_daily_budget_exceeded"
    ENQUEUE_FAILED = "work_run_enqueue_failed"
    PROVIDER_FAILED = "work_run_provider_failed"
    PROVIDER_AMBIGUOUS = "work_run_provider_ambiguous"
    VALIDATION_FAILED = "work_run_validation_failed"
    STORAGE_FAILED = "work_run_storage_failed"
    CANCEL_TOO_LATE = "work_run_cancel_too_late"
    RETRY_NOT_ALLOWED = "work_run_retry_not_allowed"
    REVISION_NOT_ALLOWED = "work_run_revision_not_allowed"
    CANCELLED = "work_run_cancelled"
    INTERNAL_ERROR = "work_run_internal_error"


class WorkRunPlanStep(StrEnum):
    READ_SOURCES = "read_sources"
    ALIGN_COLUMNS = "align_columns"
    COMBINE_ROWS = "combine_rows"
    BUILD_WORKBOOK = "build_workbook"
    VERIFY_RESULT = "verify_result"


class WorkRunOutputFeature(StrEnum):
    NATIVE_EXCEL_TABLE = "native_excel_table"
    SUMMARY_SHEET = "summary_sheet"
    SOURCES_SHEET = "sources_sheet"
    INLINE_PREVIEW = "inline_preview"


@dataclass(frozen=True)
class WorkRunDefinition:
    """Code-versioned technical constraints for one workflow kind."""

    kind: WorkRunKind
    version: int
    min_documents: int
    max_documents: int
    accepted_extensions: frozenset[str]
    artifact_kind: str
    artifact_mime_type: str
    stages: tuple[str, ...]
    plan_steps: tuple[WorkRunPlanStep, ...]


_WORK_RUN_DEFINITIONS = {
    WorkRunKind.OFFER_COMPARISON_XLSX: WorkRunDefinition(
        kind=WorkRunKind.OFFER_COMPARISON_XLSX,
        version=2,
        min_documents=2,
        max_documents=5,
        accepted_extensions=frozenset({".csv", ".xlsx"}),
        artifact_kind="offer_comparison_xlsx",
        artifact_mime_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        stages=(
            "accepted",
            "reserving_allowance",
            "waiting_for_worker",
            "loading_sources",
            "normalizing_data",
            "rendering_artifact",
            "validating_artifact",
            "storing_artifact",
            "completed",
            "cancelling",
            "cancelled",
            "failed",
        ),
        plan_steps=(
            WorkRunPlanStep.READ_SOURCES,
            WorkRunPlanStep.ALIGN_COLUMNS,
            WorkRunPlanStep.COMBINE_ROWS,
            WorkRunPlanStep.BUILD_WORKBOOK,
            WorkRunPlanStep.VERIFY_RESULT,
        ),
    ),
    WorkRunKind.SPREADSHEET_BUILDER_XLSX: WorkRunDefinition(
        kind=WorkRunKind.SPREADSHEET_BUILDER_XLSX,
        version=1,
        min_documents=1,
        max_documents=5,
        accepted_extensions=frozenset({".csv", ".xlsx"}),
        artifact_kind="spreadsheet_builder_xlsx",
        artifact_mime_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        stages=(
            "accepted",
            "reserving_allowance",
            "waiting_for_worker",
            "loading_sources",
            "normalizing_data",
            "rendering_artifact",
            "validating_artifact",
            "storing_artifact",
            "completed",
            "cancelling",
            "cancelled",
            "failed",
        ),
        plan_steps=(
            WorkRunPlanStep.READ_SOURCES,
            WorkRunPlanStep.ALIGN_COLUMNS,
            WorkRunPlanStep.COMBINE_ROWS,
            WorkRunPlanStep.BUILD_WORKBOOK,
            WorkRunPlanStep.VERIFY_RESULT,
        ),
    ),
}


_ALLOWED_STATUS_TRANSITIONS = {
    WorkRunStatus.ACCEPTED: frozenset(
        {
            WorkRunStatus.RESERVED,
            WorkRunStatus.FAILED,
            WorkRunStatus.CANCELLED,
        }
    ),
    WorkRunStatus.RESERVED: frozenset(
        {
            WorkRunStatus.QUEUED,
            WorkRunStatus.FAILED,
            WorkRunStatus.CANCELLED,
            WorkRunStatus.REFUNDED,
        }
    ),
    WorkRunStatus.QUEUED: frozenset(
        {
            WorkRunStatus.RUNNING,
            WorkRunStatus.CANCELLING,
            WorkRunStatus.FAILED,
        }
    ),
    WorkRunStatus.RUNNING: frozenset(
        {
            WorkRunStatus.VALIDATING,
            WorkRunStatus.CANCELLING,
            WorkRunStatus.FAILED,
        }
    ),
    WorkRunStatus.VALIDATING: frozenset(
        {
            WorkRunStatus.STORING,
            WorkRunStatus.FAILED,
        }
    ),
    WorkRunStatus.STORING: frozenset(
        {
            WorkRunStatus.SUCCEEDED,
            WorkRunStatus.FAILED,
        }
    ),
    WorkRunStatus.SUCCEEDED: frozenset(),
    WorkRunStatus.FAILED: frozenset({WorkRunStatus.REFUNDED}),
    WorkRunStatus.CANCELLING: frozenset(
        {WorkRunStatus.CANCELLED, WorkRunStatus.FAILED}
    ),
    WorkRunStatus.CANCELLED: frozenset({WorkRunStatus.REFUNDED}),
    WorkRunStatus.REFUNDED: frozenset(),
}


def get_work_run_definition(kind: WorkRunKind) -> WorkRunDefinition:
    return _WORK_RUN_DEFINITIONS[kind]


def list_work_run_definitions() -> tuple[WorkRunDefinition, ...]:
    return tuple(_WORK_RUN_DEFINITIONS.values())


def can_transition_work_run(
    current: WorkRunStatus,
    target: WorkRunStatus,
) -> bool:
    return target in _ALLOWED_STATUS_TRANSITIONS[current]
