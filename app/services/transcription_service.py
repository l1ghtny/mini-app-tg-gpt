from dataclasses import dataclass
from io import BytesIO

from openai import AsyncOpenAI

from app.core.config import settings


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    duration_seconds: float | None
    input_tokens: int
    output_tokens: int


_client = AsyncOpenAI()


async def transcribe_audio(
    *,
    audio: bytes,
    filename: str,
    model: str,
) -> TranscriptionResult:
    upload = BytesIO(audio)
    upload.name = filename
    response = await _client.audio.transcriptions.create(
        file=upload,
        model=model,
        response_format="json",
        timeout=settings.VOICE_TRANSCRIPTION_TIMEOUT_SECONDS,
    )

    usage = getattr(response, "usage", None)
    duration_seconds = getattr(usage, "seconds", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    return TranscriptionResult(
        text=response.text.strip(),
        duration_seconds=float(duration_seconds)
        if duration_seconds is not None
        else None,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
