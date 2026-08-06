from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.work_runs.contracts import (
    WorkRunErrorCode,
    WorkRunKind,
    WorkRunStage,
    WorkRunStatus,
)


class OfferComparisonOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    output_language: Literal["ru", "en"] = "ru"
    required_columns: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("required_columns")
    @classmethod
    def normalize_required_columns(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            value = value.strip()
            if not value:
                raise ValueError("required columns cannot be blank")
            key = value.casefold()
            if key in seen:
                raise ValueError("required columns must be unique")
            seen.add(key)
            normalized.append(value)
        return normalized


class CreateWorkRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: WorkRunKind
    conversation_id: uuid.UUID | None = None
    folder_id: uuid.UUID | None = None
    document_ids: list[uuid.UUID] = Field(min_length=2, max_length=5)
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


class WorkRunAcceptedResponse(BaseModel):
    id: uuid.UUID
    status: WorkRunStatus
    stage: WorkRunStage
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

