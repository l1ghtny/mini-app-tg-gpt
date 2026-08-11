from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.work_runs import WorkRunAcceptedResponse, WorkRunResponse


WorkExecutionKind = Literal["agentic_task", "spreadsheet_builder_xlsx"]
WorkFollowUpIntent = Literal["continue", "revise", "use_result"]


class CreateWorkThreadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=3, max_length=8000)
    document_ids: list[uuid.UUID] = Field(default_factory=list, max_length=5)
    conversation_id: uuid.UUID | None = None
    folder_id: uuid.UUID | None = None
    output_language: Literal["ru", "en"] = "ru"

    @field_validator("goal")
    @classmethod
    def normalize_goal(cls, value: str) -> str:
        value = " ".join(value.split())
        if len(value) < 3:
            raise ValueError("goal is too short")
        return value

    @field_validator("document_ids")
    @classmethod
    def unique_documents(cls, values: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(values) != len(set(values)):
            raise ValueError("document_ids must be unique")
        return values


class CreateWorkFollowUpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instruction: str = Field(min_length=3, max_length=8000)
    intent: WorkFollowUpIntent = "continue"

    @field_validator("instruction")
    @classmethod
    def normalize_instruction(cls, value: str) -> str:
        value = " ".join(value.split())
        if len(value) < 3:
            raise ValueError("instruction is too short")
        return value


class SendWorkMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=3, max_length=8000)
    document_ids: list[uuid.UUID] = Field(default_factory=list, max_length=5)
    steer_active: bool = False

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        value = " ".join(value.split())
        if len(value) < 3:
            raise ValueError("message is too short")
        return value

    @field_validator("document_ids")
    @classmethod
    def unique_documents(cls, values: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(values) != len(set(values)):
            raise ValueError("document_ids must be unique")
        return values


class WorkPlanStepResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)


class WorkExpectedOutputResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["answer", "artifact", "spreadsheet"]
    label: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=6)


class WorkPlanResponse(BaseModel):
    id: uuid.UUID
    version: int
    status: Literal["proposed", "approved", "superseded"]
    title: str
    summary: str
    execution_kind: WorkExecutionKind
    steps: list[WorkPlanStepResponse]
    expected_outputs: list[WorkExpectedOutputResponse]
    assumptions: list[str]
    created_at: datetime
    approved_at: datetime | None


class WorkThreadMessageResponse(BaseModel):
    id: uuid.UUID
    role: Literal["user", "assistant", "system"]
    kind: str
    content: str
    metadata: dict = Field(default_factory=dict)
    created_at: datetime


class WorkThreadSummaryResponse(BaseModel):
    id: uuid.UUID
    title: str
    goal: str
    status: str
    conversation_id: uuid.UUID | None
    folder_id: uuid.UUID | None
    latest_run_status: str | None = None
    created_at: datetime
    updated_at: datetime


class WorkThreadResponse(WorkThreadSummaryResponse):
    document_ids: list[uuid.UUID]
    messages: list[WorkThreadMessageResponse]
    plan: WorkPlanResponse | None
    runs: list[WorkRunResponse]


class WorkThreadListResponse(BaseModel):
    items: list[WorkThreadSummaryResponse]
    offset: int
    limit: int
    has_more: bool


class UpdateWorkPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=1200)
    steps: list[WorkPlanStepResponse] = Field(min_length=1, max_length=8)
    expected_outputs: list[WorkExpectedOutputResponse] = Field(
        min_length=1,
        max_length=4,
    )

    @model_validator(mode="after")
    def unique_step_ids(self) -> Self:
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("plan step ids must be unique")
        return self


class ApproveWorkPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_version: int = Field(ge=1)


class WorkThreadExecutionResponse(BaseModel):
    thread: WorkThreadResponse
    run: WorkRunAcceptedResponse


class WorkConversationTurnResponse(BaseModel):
    thread: WorkThreadResponse
    run: WorkRunAcceptedResponse | None = None
