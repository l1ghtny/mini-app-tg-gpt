from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.work_runs.contracts import (
    WorkRunErrorCode,
    WorkRunKind,
    WorkRunStatus,
    get_work_run_definition,
)


class OfferComparisonOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    output_language: Literal["ru", "en"] = "ru"


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


class WorkRunCapabilitiesResponse(BaseModel):
    enabled: bool
    available_kinds: list[WorkRunKind]
    max_active_per_user: int
    monthly_allowance_per_user: int
    unavailable_reason: WorkRunErrorCode | None = None


class WorkRunErrorResponse(BaseModel):
    error_code: WorkRunErrorCode
    retryable: bool = False


class ArtifactResponse(BaseModel):
    id: uuid.UUID
    work_run_id: uuid.UUID
    version: int
    kind: str
    status: str
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: datetime
    download_url: str | None = None


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


class ArtifactDownloadResponse(BaseModel):
    url: str
    expires_in: int
