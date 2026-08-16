from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.work_runs.contracts import (
    WorkRunErrorCode,
    WorkRunKind,
    WorkRunOutputFeature,
    WorkRunPlanStep,
    WorkRunStatus,
    get_work_run_definition,
)


class OfferComparisonOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    output_language: Literal["auto", "ru", "en"] = "ru"
    desired_columns: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("desired_columns")
    @classmethod
    def normalize_desired_columns(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_value in values:
            value = " ".join(raw_value.split())
            key = value.casefold()
            if not value:
                raise ValueError("desired columns cannot be blank")
            if len(value) > 120:
                raise ValueError("desired columns cannot exceed 120 characters")
            if value.startswith(("=", "+", "-", "@")):
                raise ValueError("desired columns cannot begin with a formula prefix")
            if key in seen:
                raise ValueError("desired columns must be unique")
            seen.add(key)
            normalized.append(value)
        return normalized


class CreateWorkRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: WorkRunKind
    conversation_id: uuid.UUID | None = None
    folder_id: uuid.UUID | None = None
    document_ids: list[uuid.UUID]
    instructions: str | None = Field(default=None, max_length=4000)
    options: OfferComparisonOptions = Field(default_factory=OfferComparisonOptions)

    @field_validator("document_ids")
    @classmethod
    def require_unique_documents(
        cls,
        values: list[uuid.UUID],
    ) -> list[uuid.UUID]:
        if len(values) != len(set(values)):
            raise ValueError("document_ids must be unique")
        return values

    @field_validator("instructions")
    @classmethod
    def normalize_instructions(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def enforce_workflow_document_limits(self) -> Self:
        definition = get_work_run_definition(self.kind)
        document_count = len(self.document_ids)
        if not definition.min_documents <= document_count <= definition.max_documents:
            raise ValueError(
                f"{self.kind} requires between {definition.min_documents} "
                f"and {definition.max_documents} documents"
            )
        if (
            self.kind
            in {WorkRunKind.SPREADSHEET_BUILDER_XLSX, WorkRunKind.AGENTIC_TASK}
            and self.instructions is None
        ):
            raise ValueError(f"{self.kind} requires a goal")
        if (
            self.kind == WorkRunKind.OFFER_COMPARISON_XLSX
            and self.options.desired_columns
        ):
            raise ValueError(
                "desired_columns are only supported by spreadsheet_builder_xlsx"
            )
        if (
            self.kind != WorkRunKind.AGENTIC_TASK
            and self.options.output_language == "auto"
        ):
            raise ValueError(
                "automatic output language is only supported for agentic work"
            )
        return self


class WorkRunAcceptedResponse(BaseModel):
    id: uuid.UUID
    status: WorkRunStatus
    stage: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    stream_url: str


class WorkRunPlanResponse(BaseModel):
    kind: WorkRunKind
    kind_version: int = Field(ge=1)
    min_documents: int = Field(ge=0)
    max_documents: int = Field(ge=1)
    steps: list[WorkRunPlanStep]


class WorkRunUsageResponse(BaseModel):
    monthly_used: int = Field(ge=0)
    monthly_allowance: int = Field(ge=0)
    monthly_remaining: int = Field(ge=0)
    monthly_resets_at: datetime
    active_runs: int = Field(ge=0)
    max_active_runs: int = Field(ge=0)
    can_start: bool
    blocking_reason: WorkRunErrorCode | None = None


class WorkRunCapabilitiesResponse(BaseModel):
    enabled: bool
    available_kinds: list[WorkRunKind]
    max_active_per_user: int
    monthly_allowance_per_user: int
    unavailable_reason: WorkRunErrorCode | None = None
    plans: list[WorkRunPlanResponse] = Field(default_factory=list)
    usage: WorkRunUsageResponse | None = None


class SpreadsheetWorkRunResultSummary(BaseModel):
    version: Literal[1] = 1
    rows: int = Field(ge=0)
    columns: int = Field(ge=0)
    sources: int = Field(ge=0)
    normalization_mode: Literal["model", "exact"]
    output_features: list[WorkRunOutputFeature]


class WorkRunErrorResponse(BaseModel):
    error_code: WorkRunErrorCode
    message: str
    retryable: bool = False
    usage: WorkRunUsageResponse | None = None


class ReviseArtifactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instructions: str = Field(min_length=1, max_length=4000)

    @field_validator("instructions")
    @classmethod
    def normalize_instructions(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("revision instructions cannot be blank")
        return value


class ArtifactSourceResponse(BaseModel):
    document_id: uuid.UUID | None
    title: str | None
    sheet_name: str | None
    row_start: int | None
    row_end: int | None
    ordinal: int


class ArtifactResponse(BaseModel):
    id: uuid.UUID
    work_run_id: uuid.UUID
    parent_artifact_id: uuid.UUID | None
    version: int
    kind: str
    status: str
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str | None = None
    metadata: dict = Field(default_factory=dict)
    sources: list[ArtifactSourceResponse] = Field(default_factory=list)
    created_at: datetime
    download_url: str | None = None


class WorkRunActivityEventResponse(BaseModel):
    id: uuid.UUID
    sequence: int = Field(ge=1)
    kind: str = Field(min_length=1, max_length=32)
    status: Literal["active", "completed", "failed", "cancelled"]
    phase: str | None = Field(default=None, max_length=32)
    title: str | None = Field(default=None, max_length=240)
    detail: str | None = Field(default=None, max_length=1000)
    metadata: dict = Field(default_factory=dict)
    started_at: datetime
    completed_at: datetime | None = None


class WorkRunResponse(BaseModel):
    id: uuid.UUID
    kind: WorkRunKind
    kind_version: int
    status: WorkRunStatus
    stage: str
    progress_percent: int | None
    conversation_id: uuid.UUID | None
    folder_id: uuid.UUID | None
    instructions: str | None
    options: dict
    result_summary: str | None
    retry_of_work_run_id: uuid.UUID | None = None
    reserved_units: Decimal
    estimated_cost_usd: Decimal
    actual_cost_usd: Decimal
    error_code: WorkRunErrorCode | None
    error_message: str | None
    queued_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    created_at: datetime
    updated_at: datetime
    artifacts: list[ArtifactResponse] = Field(default_factory=list)
    activity_events: list[WorkRunActivityEventResponse] = Field(default_factory=list)


class WorkRunListResponse(BaseModel):
    items: list[WorkRunResponse]
    offset: int
    limit: int
    has_more: bool


class ArtifactDownloadResponse(BaseModel):
    url: str
    expires_in: int


class ArtifactInlinePreviewResponse(BaseModel):
    kind: Literal["image", "pdf", "text"]
    mime_type: str
    url: str | None = None
    content: str | None = None
    truncated: bool = False
    expires_in: int | None = None

    @model_validator(mode="after")
    def validate_preview_payload(self) -> Self:
        if self.kind in {"image", "pdf"} and not self.url:
            raise ValueError("binary inline previews require a URL")
        if self.kind == "text" and self.content is None:
            raise ValueError("text inline previews require content")
        return self


ArtifactPreviewCell = str | int | float | bool | None


class ArtifactPreviewColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=120)
    data_type: Literal["text", "number", "date", "datetime", "boolean"]
    number_format: str | None = Field(default=None, max_length=64)


class ArtifactPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    goal: str | None = Field(default=None, max_length=4000)
    row_count: int = Field(ge=0, le=250_000)
    column_count: int = Field(ge=0)
    source_count: int = Field(ge=0)
    columns: list[ArtifactPreviewColumn] = Field(max_length=30)
    rows: list[list[ArtifactPreviewCell]] = Field(max_length=100)
    rows_truncated: bool
    columns_truncated: bool
    warning_codes: list[
        Literal[
            "preview_rows_truncated",
            "preview_columns_truncated",
            "preview_cells_truncated",
        ]
    ] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def validate_preview_matrix(self) -> Self:
        width = len(self.columns)
        if self.column_count < width:
            raise ValueError("preview cannot contain more columns than the artifact")
        if self.row_count < len(self.rows):
            raise ValueError("preview cannot contain more rows than the artifact")
        for row in self.rows:
            if len(row) != width:
                raise ValueError("preview rows must match the preview columns")
            for value in row:
                if isinstance(value, str) and len(value) > 500:
                    raise ValueError("preview cell text cannot exceed 500 characters")
        if self.rows_truncated != (self.row_count > len(self.rows)):
            raise ValueError("rows_truncated does not match the preview size")
        if self.columns_truncated != (self.column_count > width):
            raise ValueError("columns_truncated does not match the preview size")
        return self
