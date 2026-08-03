from pydantic import BaseModel


class AudioTranscriptionResponse(BaseModel):
    request_id: str
    text: str
    model: str
    duration_seconds: float
    cached: bool = False
