"""Durable, single-attempt processing for uploaded audio transcription."""

import asyncio
import logging
import os
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from openai import APIConnectionError, APIStatusError, APITimeoutError, AuthenticationError
from redis.asyncio import Redis
from sqlalchemy import func, text
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.db.database import engine
from app.db.models import AudioTranscriptionJob, AppUser, RequestLedger, State, TokenUsage, utcnow_naive
from app.r2.private_audio import delete_audio, download_audio
from app.redis.settings import settings as redis_settings
from app.schemas.audio import AudioTranscriptionJobResponse
from app.services.transcription_service import transcribe_audio

logger = logging.getLogger(__name__)
_TERMINAL = ("completed", "failed", "uncertain")
_RESULT_RETENTION = timedelta(hours=24)
_QUEUE_WAIT = timedelta(hours=1)
_LEASE_GRACE = timedelta(minutes=5)
_CLAIM_LOCK_ID = 482091734
HEARTBEAT_PATH = Path("/tmp/audio-worker-heartbeat")


def job_response(job: AudioTranscriptionJob) -> AudioTranscriptionJobResponse:
    completed = job.status == "completed"
    return AudioTranscriptionJobResponse(
        request_id=str(job.request_id),
        status="queued" if job.status == "storing" else ("failed" if job.status == "cancel_pending" else job.status),
        status_url=f"/api/v1/audio/transcriptions/{job.request_id}",
        text=job.transcript_text if completed else None,
        model=job.model_name if completed else None,
        duration_seconds=job.duration_seconds if completed else None,
        error_code=job.error_code if job.status in ("failed", "uncertain", "cancel_pending") else None,
    )


async def claim_next_job(session: AsyncSession) -> AudioTranscriptionJob | None:
    # The transaction-scoped advisory lock serializes claims across replicas.
    got_lock = (await session.exec(text("SELECT pg_try_advisory_xact_lock(:key)"),
                                   params={"key": _CLAIM_LOCK_ID})).scalar_one()
    if not got_lock:
        await session.rollback()
        return None
    now = utcnow_naive()
    active = (await session.exec(select(func.count()).select_from(AudioTranscriptionJob).where(
        AudioTranscriptionJob.channel == settings.DEPLOYMENT_CHANNEL,
        AudioTranscriptionJob.status == "processing",
        AudioTranscriptionJob.lease_expires_at > now,
    ))).one()
    if active >= 1:
        await session.rollback()
        return None
    job = (await session.exec(select(AudioTranscriptionJob).where(
        AudioTranscriptionJob.channel == settings.DEPLOYMENT_CHANNEL,
        AudioTranscriptionJob.status == "queued",
        AudioTranscriptionJob.created_at >= now - _QUEUE_WAIT,
    ).order_by(AudioTranscriptionJob.created_at).with_for_update(skip_locked=True).limit(1))).first()
    if job is None:
        await session.rollback()
        return None
    job.status = "processing"
    job.attempt_started_at = now
    # R2 download has its own five-minute bound before the provider attempt.
    job.lease_expires_at = (
        now + timedelta(seconds=settings.VOICE_TRANSCRIPTION_UPLOAD_TIMEOUT_SECONDS)
        + 2 * _LEASE_GRACE
    )
    job.updated_at = now
    session.add(job)
    await session.commit()
    return job


async def _terminal(job_id: uuid.UUID, status: str, error_code: str) -> None:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        job = (await session.exec(select(AudioTranscriptionJob).where(
            AudioTranscriptionJob.id == job_id,
            AudioTranscriptionJob.channel == settings.DEPLOYMENT_CHANNEL,
        ).with_for_update())).first()
        if job is None or job.status in _TERMINAL:
            return
        ledger = (await session.exec(select(RequestLedger).where(
            RequestLedger.user_id == job.user_id,
            RequestLedger.request_id == str(job.request_id),
        ).with_for_update())).first()
        if ledger and ledger.state == State.reserved:
            ledger.state = State.failed
            session.add(ledger)
        job.status = status
        job.error_code = error_code
        job.lease_expires_at = None
        job.updated_at = utcnow_naive()
        job.expires_at = job.updated_at + _RESULT_RETENTION
        session.add(job)
        await session.commit()


async def _complete(job_id: uuid.UUID, result) -> None:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        identity = (await session.exec(select(AudioTranscriptionJob.user_id).where(
            AudioTranscriptionJob.id == job_id,
            AudioTranscriptionJob.channel == settings.DEPLOYMENT_CHANNEL,
        ))).first()
        if identity is None:
            return
        user = (await session.exec(select(AppUser).where(AppUser.id == identity).with_for_update())).one()
        job = (await session.exec(select(AudioTranscriptionJob).where(
            AudioTranscriptionJob.id == job_id,
            AudioTranscriptionJob.channel == settings.DEPLOYMENT_CHANNEL,
        ).with_for_update())).first()
        if job is None:
            return
        if job.status != "processing":
            return
        if user.deleted_at is not None:
            await session.rollback()
            await _terminal(job_id, "failed", "voice_transcription_account_deleted")
            return
        ledger = (await session.exec(select(RequestLedger).where(
            RequestLedger.user_id == job.user_id,
            RequestLedger.request_id == str(job.request_id),
        ).with_for_update())).one()
        if ledger.state != State.reserved:
            raise RuntimeError("Transcription ledger reservation is unavailable")
        ledger.state = State.consumed
        ledger.cost = job.duration_seconds / 60
        job.status = "completed"
        job.transcript_text = result.text
        job.error_code = None
        job.lease_expires_at = None
        job.updated_at = utcnow_naive()
        job.expires_at = job.updated_at + _RESULT_RETENTION
        session.add(ledger)
        session.add(job)
        session.add(TokenUsage(
            user_id=job.user_id, provider="openai", model_name=job.model_name,
            request_id=str(job.request_id), status="success",
            input_tokens=result.input_tokens, output_tokens=result.output_tokens,
            total_cost=Decimal(settings.VOICE_TRANSCRIPTION_COST_PER_MINUTE_USD)
            * Decimal(str(job.duration_seconds)) / Decimal("60"),
        ))
        await session.commit()


async def cleanup_audio_object(job_id: uuid.UUID) -> None:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        job = (await session.exec(select(AudioTranscriptionJob).where(
            AudioTranscriptionJob.id == job_id,
            AudioTranscriptionJob.channel == settings.DEPLOYMENT_CHANNEL,
        ))).first()
        if job is None or job.status not in _TERMINAL or not job.audio_key:
            return
        key = job.audio_key
    try:
        await delete_audio(key)
    except Exception:
        logger.exception("Could not delete transcription input job_id=%s", job_id)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            current = (await session.exec(select(AudioTranscriptionJob).where(
                AudioTranscriptionJob.id == job_id,
                AudioTranscriptionJob.channel == settings.DEPLOYMENT_CHANNEL,
            ).with_for_update())).first()
            if current and current.audio_key == key:
                current.updated_at = utcnow_naive()
                session.add(current)
                await session.commit()
        return
    async with AsyncSession(engine, expire_on_commit=False) as session:
        job = (await session.exec(select(AudioTranscriptionJob).where(
            AudioTranscriptionJob.id == job_id,
            AudioTranscriptionJob.channel == settings.DEPLOYMENT_CHANNEL,
        ).with_for_update())).first()
        if job and job.audio_key == key and job.status in _TERMINAL:
            job.audio_key = None
            session.add(job)
            await session.commit()


async def process_job(job: AudioTranscriptionJob) -> None:
    # Processing is durable before the provider call. A crash cannot trigger
    # automatic reprocessing of an uncertain paid operation.
    provider_started = False
    try:
        if not job.audio_key:
            await _terminal(job.id, "failed", "voice_transcription_audio_unavailable")
            return
        contents = await download_audio(job.audio_key, max_bytes=settings.VOICE_TRANSCRIPTION_UPLOAD_MAX_BYTES)
        provider_started = True
        async with asyncio.timeout(settings.VOICE_TRANSCRIPTION_UPLOAD_TIMEOUT_SECONDS):
            result = await transcribe_audio(
                audio=contents, filename=f"audio{job.audio_extension}", model=job.model_name,
                timeout_seconds=settings.VOICE_TRANSCRIPTION_UPLOAD_TIMEOUT_SECONDS,
            )
        if not result.text:
            await _terminal(job.id, "failed", "voice_transcription_no_speech")
        else:
            await _complete(job.id, result)
    except (AuthenticationError, APIStatusError) as exc:
        logger.warning("Audio provider rejected job_id=%s type=%s", job.id, type(exc).__name__)
        code = "voice_transcription_audio_invalid" if isinstance(exc, APIStatusError) and exc.status_code in (400, 413, 415, 422) else "voice_transcription_provider_unavailable"
        await _terminal(job.id, "failed", code)
    except (TimeoutError, APITimeoutError, APIConnectionError) as exc:
        logger.warning("Audio provider outcome uncertain job_id=%s type=%s", job.id, type(exc).__name__)
        await _terminal(job.id, "uncertain", "voice_transcription_result_uncertain")
    except Exception:
        logger.exception("Audio transcription job failed job_id=%s", job.id)
        await _terminal(job.id, "uncertain" if provider_started else "failed",
                        "voice_transcription_result_uncertain" if provider_started else "voice_transcription_audio_unavailable")
    finally:
        await cleanup_audio_object(job.id)


async def maintain_jobs() -> None:
    now = utcnow_naive()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        stale = (await session.exec(select(AudioTranscriptionJob.id, AudioTranscriptionJob.status).where(
            AudioTranscriptionJob.channel == settings.DEPLOYMENT_CHANNEL,
            ((AudioTranscriptionJob.status.in_(("storing", "queued"))) & (AudioTranscriptionJob.created_at < now - _QUEUE_WAIT))
            | ((AudioTranscriptionJob.status == "processing") & (AudioTranscriptionJob.lease_expires_at < now)),
        ).limit(25))).all()
        cancelled = (await session.exec(select(AudioTranscriptionJob.id).where(
            AudioTranscriptionJob.channel == settings.DEPLOYMENT_CHANNEL,
            AudioTranscriptionJob.status == "cancel_pending",
            # The original HTTP request may still have an in-flight R2 PUT.
            # Its five-minute bound is well inside this hour; failed deletion
            # attempts then back off for one minute.
            AudioTranscriptionJob.created_at < now - timedelta(hours=1),
            AudioTranscriptionJob.updated_at < now - timedelta(minutes=1),
        ).limit(25))).all()
        to_clean = (await session.exec(select(AudioTranscriptionJob.id).where(
            AudioTranscriptionJob.channel == settings.DEPLOYMENT_CHANNEL,
            AudioTranscriptionJob.status.in_(_TERMINAL),
            AudioTranscriptionJob.audio_key.is_not(None),
        ).order_by(AudioTranscriptionJob.updated_at).limit(25))).all()
        expired = (await session.exec(select(AudioTranscriptionJob).where(
            AudioTranscriptionJob.channel == settings.DEPLOYMENT_CHANNEL,
            AudioTranscriptionJob.status.in_(_TERMINAL),
            AudioTranscriptionJob.expires_at < now,
            (AudioTranscriptionJob.audio_key.is_(None))
            | (AudioTranscriptionJob.transcript_text.is_not(None)),
        ).limit(25).with_for_update(skip_locked=True))).all()
        for job in expired:
            if job.audio_key:
                # Keep the key for deletion retries, but never retain result
                # text past the advertised 24-hour recovery window.
                job.transcript_text = None
                session.add(job)
            else:
                await session.delete(job)
        await session.commit()
    for job_id, state in stale:
        await _terminal(job_id, "uncertain" if state == "processing" else "failed",
                        "voice_transcription_result_uncertain" if state == "processing" else "voice_transcription_queue_timeout")
        await cleanup_audio_object(job_id)
    for job_id in cancelled:
        await cleanup_cancelled_object(job_id)
    for job_id in to_clean:
        await cleanup_audio_object(job_id)


async def cleanup_cancelled_object(job_id: uuid.UUID) -> None:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        job = (await session.exec(select(AudioTranscriptionJob).where(
            AudioTranscriptionJob.id == job_id,
            AudioTranscriptionJob.channel == settings.DEPLOYMENT_CHANNEL,
            AudioTranscriptionJob.status == "cancel_pending",
        ))).first()
        if not job:
            return
        key = job.audio_key
    if key:
        try:
            await delete_audio(key)
        except Exception:
            logger.exception("Could not delete cancelled audio job_id=%s", job_id)
            async with AsyncSession(engine, expire_on_commit=False) as session:
                current = (await session.exec(select(AudioTranscriptionJob).where(
                    AudioTranscriptionJob.id == job_id,
                    AudioTranscriptionJob.channel == settings.DEPLOYMENT_CHANNEL,
                    AudioTranscriptionJob.status == "cancel_pending",
                ).with_for_update())).first()
                if current and current.audio_key == key:
                    current.updated_at = utcnow_naive()
                    session.add(current)
                    await session.commit()
            return
    async with AsyncSession(engine, expire_on_commit=False) as session:
        job = (await session.exec(select(AudioTranscriptionJob).where(
            AudioTranscriptionJob.id == job_id,
            AudioTranscriptionJob.channel == settings.DEPLOYMENT_CHANNEL,
            AudioTranscriptionJob.status == "cancel_pending",
        ).with_for_update())).first()
        if job:
            await session.delete(job)
            await session.commit()


async def _heartbeat(stop_event: asyncio.Event) -> None:
    redis = Redis.from_url(redis_settings.REDIS_URL, decode_responses=True)
    try:
        while not stop_event.is_set():
            HEARTBEAT_PATH.touch()
            try:
                await redis.set(f"voice:transcription:worker:{settings.DEPLOYMENT_CHANNEL}", "1", ex=30)
            except Exception:
                logger.exception("Could not publish audio worker heartbeat")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=10)
            except TimeoutError:
                pass
    finally:
        await redis.aclose()


async def run_worker(stop_event: asyncio.Event) -> None:
    heartbeat = asyncio.create_task(_heartbeat(stop_event))
    maintenance: asyncio.Task | None = None
    try:
        while not stop_event.is_set():
            try:
                if maintenance is None or maintenance.done():
                    if maintenance is not None:
                        try:
                            await maintenance
                        except Exception:
                            logger.exception("Audio transcription maintenance failed")
                    # Private-object deletion can be slow or unavailable. Keep
                    # it bounded and independent of new job claims.
                    maintenance = asyncio.create_task(
                        asyncio.wait_for(maintain_jobs(), timeout=30)
                    )
                async with AsyncSession(engine, expire_on_commit=False) as session:
                    job = await claim_next_job(session)
                if job:
                    await process_job(job)
                else:
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=2)
                    except TimeoutError:
                        pass
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Audio transcription worker loop failed")
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=2)
                except TimeoutError:
                    pass
    finally:
        if maintenance is not None:
            maintenance.cancel()
            await asyncio.gather(maintenance, return_exceptions=True)
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        try:
            os.unlink(HEARTBEAT_PATH)
        except FileNotFoundError:
            pass
