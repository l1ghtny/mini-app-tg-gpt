"""Short-lived transcription inputs in the existing private document bucket."""

import uuid
import asyncio
from datetime import datetime, timezone

import aioboto3
from botocore.config import Config

from app.r2.settings import Settings


class PrivateAudioStorageConfigurationError(RuntimeError):
    pass


def audio_key(channel: str, user_id: uuid.UUID, job_id: uuid.UUID, extension: str) -> str:
    return f"transcriptions/{channel}/{user_id}/{job_id}{extension}"


def _bucket() -> str:
    bucket = Settings.AUDIO_TRANSCRIPTION_R2_BUCKET
    if not bucket or bucket == Settings.R2_BUCKET or not all((
        Settings.AUDIO_TRANSCRIPTION_R2_ENDPOINT,
        Settings.AUDIO_TRANSCRIPTION_R2_ACCESS_KEY_ID,
        Settings.AUDIO_TRANSCRIPTION_R2_SECRET_ACCESS_KEY,
    )):
        raise PrivateAudioStorageConfigurationError("private audio storage is not configured")
    expiry = Settings.AUDIO_TRANSCRIPTION_R2_CREDENTIAL_EXPIRES_AT
    if expiry:
        try:
            parsed = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PrivateAudioStorageConfigurationError("audio storage credential expiry is invalid") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if parsed <= datetime.now(timezone.utc):
            raise PrivateAudioStorageConfigurationError("audio storage credentials have expired")
    return bucket


def ensure_audio_storage_configured() -> None:
    _bucket()


def _client():
    return aioboto3.Session().client(
        "s3",
        endpoint_url=Settings.AUDIO_TRANSCRIPTION_R2_ENDPOINT,
        region_name=Settings.AUDIO_TRANSCRIPTION_R2_REGION,
        aws_access_key_id=Settings.AUDIO_TRANSCRIPTION_R2_ACCESS_KEY_ID,
        aws_secret_access_key=Settings.AUDIO_TRANSCRIPTION_R2_SECRET_ACCESS_KEY,
        aws_session_token=Settings.AUDIO_TRANSCRIPTION_R2_SESSION_TOKEN or None,
        config=Config(
            signature_version="s3v4",
            connect_timeout=10,
            read_timeout=60,
            retries={"max_attempts": 2, "mode": "standard"},
            proxies={},
        ),
    )


async def upload_audio(key: str, contents: bytes) -> None:
    bucket = _bucket()
    async with asyncio.timeout(300):
        async with _client() as client:
            await client.put_object(
                Bucket=bucket, Key=key, Body=contents,
                ContentType="application/octet-stream",
            )


async def download_audio(key: str, *, max_bytes: int) -> bytes:
    bucket = _bucket()
    async with asyncio.timeout(300):
        async with _client() as client:
            response = await client.get_object(Bucket=bucket, Key=key)
            body = response["Body"]
            try:
                length = response.get("ContentLength")
                if isinstance(length, int) and length > max_bytes:
                    raise ValueError("private audio object is oversized")
                parts = []
                size = 0
                while size <= max_bytes:
                    chunk = await body.read(min(1024 * 1024, max_bytes + 1 - size))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("private audio object is oversized")
                    parts.append(chunk)
                contents = b"".join(parts)
            finally:
                body.close()
            if not contents:
                raise ValueError("private audio object is missing")
            return contents


async def delete_audio(key: str) -> None:
    bucket = _bucket()
    async with asyncio.timeout(120):
        async with _client() as client:
            await client.delete_object(Bucket=bucket, Key=key)
