from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping


class WorkRunKind(StrEnum):
    OFFER_COMPARISON_XLSX = "offer_comparison_xlsx"


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


class WorkRunStage(StrEnum):
    ACCEPTED = "accepted"
    RESERVING_ALLOWANCE = "reserving_allowance"
    WAITING_FOR_WORKER = "waiting_for_worker"
    LOADING_SOURCES = "loading_sources"
    NORMALIZING_DATA = "normalizing_data"
    RENDERING_ARTIFACT = "rendering_artifact"
    VALIDATING_ARTIFACT = "validating_artifact"
    STORING_ARTIFACT = "storing_artifact"
    COMPLETED = "completed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    FAILED = "failed"


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
    CANCELLED = "work_run_cancelled"
    INTERNAL_ERROR = "work_run_internal_error"


@dataclass(frozen=True)
class WorkRunDefinition:
    kind: WorkRunKind
    version: int
    min_documents: int
    max_documents: int
    accepted_extensions: frozenset[str]
    artifact_kind: str
    artifact_mime_type: str
    reserved_units: int


WORK_RUN_DEFINITIONS: Mapping[WorkRunKind, WorkRunDefinition] = MappingProxyType(
    {
        WorkRunKind.OFFER_COMPARISON_XLSX: WorkRunDefinition(
            kind=WorkRunKind.OFFER_COMPARISON_XLSX,
            version=1,
            min_documents=2,
            max_documents=5,
            accepted_extensions=frozenset({".csv", ".xlsx"}),
            artifact_kind="offer_comparison_xlsx",
            artifact_mime_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            reserved_units=1,
        )
    }
)


_ALLOWED_STATUS_TRANSITIONS: Mapping[WorkRunStatus, frozenset[WorkRunStatus]] = (
    MappingProxyType(
        {
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
                    WorkRunStatus.CANCELLING,
                    WorkRunStatus.FAILED,
                }
            ),
            WorkRunStatus.STORING: frozenset(
                {
                    WorkRunStatus.SUCCEEDED,
                    WorkRunStatus.CANCELLING,
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
    )
)


def get_work_run_definition(kind: WorkRunKind) -> WorkRunDefinition:
    return WORK_RUN_DEFINITIONS[kind]


def can_transition_work_run(
    current: WorkRunStatus,
    target: WorkRunStatus,
) -> bool:
    return target in _ALLOWED_STATUS_TRANSITIONS[current]

