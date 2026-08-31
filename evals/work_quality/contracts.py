from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ClarificationExpectation = Literal["allowed", "forbidden", "required"]
InteractionAction = Literal["answer", "cancel", "message", "retry"]
WaitCondition = Literal["running", "terminal", "waiting_for_user"]


class EvalAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)

    @field_validator("path")
    @classmethod
    def require_relative_path(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("attachment paths must stay inside the eval suite")
        return path.as_posix()


class EvalInteraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: InteractionAction
    content: str | None = Field(default=None, max_length=8000)
    wait_for: WaitCondition = "terminal"
    delay_seconds: float = Field(default=0, ge=0, le=30)

    @model_validator(mode="after")
    def validate_content(self) -> EvalInteraction:
        needs_content = self.action in {"answer", "message"}
        if needs_content and not (self.content or "").strip():
            raise ValueError(f"{self.action} interactions require content")
        if not needs_content and self.content is not None:
            raise ValueError(f"{self.action} interactions cannot include content")
        return self


class EvalExpectations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    terminal_statuses: list[str] = Field(default_factory=lambda: ["succeeded"])
    min_successful_runs: int = Field(default=1, ge=0, le=10)
    min_result_characters: int = Field(default=120, ge=0, le=20_000)
    response_language: Literal["en", "ru", "any"] = "en"
    clarification: ClarificationExpectation = "allowed"
    required_tools: list[Literal["code_interpreter", "file_search", "web_search"]] = (
        Field(default_factory=list)
    )
    forbidden_tools: list[Literal["code_interpreter", "file_search", "web_search"]] = (
        Field(default_factory=list)
    )
    min_sources: int = Field(default=0, ge=0, le=50)
    min_citations: int = Field(default=0, ge=0, le=100)
    min_artifacts: int = Field(default=0, ge=0, le=10)
    artifact_extensions: list[str] = Field(default_factory=list)
    max_cost_usd: float | None = Field(default=None, gt=0, le=100)
    max_duration_seconds: float | None = Field(default=None, gt=0, le=7200)

    @field_validator("terminal_statuses", "required_tools", "forbidden_tools")
    @classmethod
    def require_unique_values(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("expectation values must be unique")
        return values

    @field_validator("artifact_extensions")
    @classmethod
    def normalize_extensions(cls, values: list[str]) -> list[str]:
        normalized = [
            value.lower() if value.startswith(".") else f".{value.lower()}"
            for value in values
        ]
        if len(normalized) != len(set(normalized)):
            raise ValueError("artifact extensions must be unique")
        return normalized

    @model_validator(mode="after")
    def disjoint_tool_expectations(self) -> EvalExpectations:
        overlap = set(self.required_tools) & set(self.forbidden_tools)
        if overlap:
            raise ValueError(f"tools cannot be both required and forbidden: {overlap}")
        if self.min_artifacts < len(self.artifact_extensions):
            raise ValueError("min_artifacts cannot be smaller than required extensions")
        return self


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    category: Literal[
        "artifact",
        "clarification",
        "documents",
        "recovery",
        "revision",
        "spreadsheet",
        "web_research",
    ]
    title: str = Field(min_length=3, max_length=160)
    goal: str = Field(min_length=3, max_length=8000)
    output_language: Literal["auto", "ru", "en"] = "auto"
    attachments: list[EvalAttachment] = Field(default_factory=list, max_length=5)
    interactions: list[EvalInteraction] = Field(default_factory=list, max_length=5)
    expectations: EvalExpectations
    human_rubric: list[str] = Field(min_length=3, max_length=8)
    tags: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("human_rubric", "tags")
    @classmethod
    def require_unique_text(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("case values must be unique")
        return values


class EvalSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    name: str = Field(min_length=3, max_length=120)
    description: str = Field(min_length=10, max_length=1000)
    cases: list[EvalCase] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_case_ids(self) -> EvalSuite:
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("eval case ids must be unique")
        return self


class ArtifactObservation(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    filename: str
    mime_type: str | None = None
    size_bytes: int = Field(default=0, ge=0)
    status: str = "unknown"
    sha256: str | None = None
    content_path: str | None = None
    download_error: str | None = None


class RunObservation(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    status: str
    stage: str | None = None
    result_text: str = ""
    error_code: str | None = None
    error_message: str | None = None
    actual_cost_usd: float = Field(default=0, ge=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    tool_counts: dict[str, int] = Field(default_factory=dict)
    sources: list[dict] = Field(default_factory=list)
    citations: list[dict] = Field(default_factory=list)
    artifacts: list[ArtifactObservation] = Field(default_factory=list)
    clarification_count: int = Field(default=0, ge=0)


class EvalObservation(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: Literal[1] = 1
    suite_version: int = Field(ge=1)
    case_id: str
    environment: str
    started_at: datetime
    completed_at: datetime
    thread_id: str | None = None
    runs: list[RunObservation] = Field(default_factory=list)
    api_errors: list[str] = Field(default_factory=list)

    @classmethod
    def empty(
        cls, *, suite_version: int, case_id: str, environment: str
    ) -> EvalObservation:
        now = datetime.now(timezone.utc)
        return cls(
            suite_version=suite_version,
            case_id=case_id,
            environment=environment,
            started_at=now,
            completed_at=now,
        )


class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    passed: bool
    required: bool = True
    detail: str


class HumanAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    reviewed: bool = False
    usefulness: int = Field(ge=1, le=5)
    correctness: int = Field(ge=1, le=5)
    completeness: int = Field(ge=1, le=5)
    readability: int = Field(ge=1, le=5)
    needed_correction: bool
    notes: str = Field(default="", max_length=4000)


class CaseScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    automated_passed: bool
    automated_score: float = Field(ge=0, le=1)
    checks: list[CheckResult]
    human: HumanAssessment | None = None


class EvalReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    suite_version: int
    generated_at: datetime
    environment: str
    case_scores: list[CaseScore]
    metrics: dict[str, float | int | None]
