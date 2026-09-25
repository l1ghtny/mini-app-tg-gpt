from pydantic import BaseModel
from typing import Literal
from datetime import datetime


class AudioTranscriptionResponse(BaseModel):
    request_id: str
    text: str
    model: str
    duration_seconds: float
    cached: bool = False


class AudioTranscriptionLimits(BaseModel):
    entitled: bool
    monthly_limit_minutes: float
    resets_at: datetime | None
    upload_available: bool
    max_bytes: int
    max_duration_seconds: int
    remaining_minutes: float
    allowed_extensions: list[str]
    recording_max_bytes: int
    recording_max_duration_seconds: int


class AudioTranscriptionJobResponse(BaseModel):
    request_id: str
    status: Literal["queued", "processing", "completed", "failed", "uncertain"]
    status_url: str
    text: str | None = None
    model: str | None = None
    duration_seconds: float | None = None
    error_code: str | None = None
