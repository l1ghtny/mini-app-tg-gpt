import json
import logging
import uuid
from typing import Literal
from datetime import UTC, datetime
from datetime import timedelta
from decimal import Decimal
from asyncio import to_thread

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
)
from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func, text as sql_text
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.dependencies import get_current_user, get_redis
from app.core.config import settings
from app.db.database import get_session
from app.db.models import AudioTranscriptionJob, AppUser, RequestLedger, State, TokenUsage
from app.db.subscription_tiers import (
    SubscriptionStatus,
    SubscriptionTier,
    UserSubscription,
)
from app.schemas.audio import AudioTranscriptionJobResponse, AudioTranscriptionLimits, AudioTranscriptionResponse
from app.r2.private_audio import (
    PrivateAudioStorageConfigurationError, audio_key, delete_audio,
    ensure_audio_storage_configured, upload_audio,
)
from app.services.audio_probe import InvalidAudioError, probe_audio
from app.services.audio_jobs import job_response
from app.services.transcription_service import TranscriptionResult, transcribe_audio


audio = APIRouter(tags=["audio"], prefix="/audio")
logger = logging.getLogger(__name__)

_ALLOWED_EXTENSIONS = ["mp3", "m4a", "wav", "webm"]
_UPLOAD_PENDING = ("storing", "queued", "processing")


def _cache_key(user_id: uuid.UUID, request_id: str) -> str:
    return f"voice:transcription:result:{user_id}:{request_id}"


def _lock_key(user_id: uuid.UUID, request_id: str) -> str:
    return f"voice:transcription:lock:{user_id}:{request_id}"


def _quota_lock_key(user_id: uuid.UUID) -> str:
    return f"voice:transcription:quota-lock:{user_id}"


async def _release_lock(redis: Redis, key: str, token: str) -> None:
    try:
        await redis.eval(
            'if redis.call("get", KEYS[1]) == ARGV[1] then '
            'return redis.call("del", KEYS[1]) else return 0 end',
            1, key, token,
        )
    except Exception:
        logger.exception("Transcription lock release failed key=%s", key)


def _utc_month_start() -> datetime:
    return datetime.now(UTC).replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
        tzinfo=None,
    )


async def _get_transcription_tier(
    session: AsyncSession, user_id: uuid.UUID
) -> SubscriptionTier | None:
    statement = (
        select(SubscriptionTier)
        .join(UserSubscription, UserSubscription.tier_id == SubscriptionTier.id)
        .where(
            UserSubscription.user_id == user_id,
            UserSubscription.status == SubscriptionStatus.active,
            (UserSubscription.expires_at.is_(None))
            | (UserSubscription.expires_at > func.now())
            | (
                UserSubscription.renewal_grace_until.is_not(None)
                & (UserSubscription.renewal_grace_until > func.now())
            ),
            SubscriptionTier.monthly_transcription_minutes > 0,
        )
        .order_by(
            SubscriptionTier.monthly_transcription_minutes.desc(),
            SubscriptionTier.price_cents.desc(),
            UserSubscription.started_at.desc(),
        )
        .limit(1)
    )
    return (await session.exec(statement)).first()


async def _used_transcription_minutes(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    tier_id: uuid.UUID,
    exclude_request_id: str,
) -> float:
    statement = select(func.coalesce(func.sum(RequestLedger.cost), 0)).where(
        RequestLedger.user_id == user_id,
        RequestLedger.tier_id == tier_id,
        RequestLedger.feature == "transcription",
        RequestLedger.state.in_((State.reserved, State.consumed)),
        RequestLedger.created_at >= _utc_month_start(),
        RequestLedger.request_id != exclude_request_id,
    )
    return float((await session.exec(statement)).one() or 0)


async def _cached_response(
    redis: Redis, user_id: uuid.UUID, request_id: str
) -> AudioTranscriptionResponse | None:
    raw = await redis.get(_cache_key(user_id, request_id))
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        payload["cached"] = True
        return AudioTranscriptionResponse.model_validate(payload)
    except (json.JSONDecodeError, TypeError, ValueError):
        await redis.delete(_cache_key(user_id, request_id))
        return None


async def _rate_limit(redis: Redis, user_id: uuid.UUID) -> None:
    key = f"rl:voice-transcription:{user_id}"
    current = await redis.incr(key)
    if current == 1:
        await redis.expire(key, 3600)
    if current > settings.VOICE_TRANSCRIPTION_RATE_LIMIT_PER_HOUR:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="voice_transcription_rate_limited",
        )


async def _reserve_ledger(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    tier_id: uuid.UUID,
    request_id: str,
    model: str,
    estimated_minutes: float,
) -> RequestLedger:
    existing = (
        await session.exec(
            select(RequestLedger).where(
                RequestLedger.user_id == user_id,
                RequestLedger.request_id == request_id,
            )
        )
    ).first()
    if existing:
        if existing.feature != "transcription":
            raise HTTPException(status_code=409, detail="request_id_conflict")
        if existing.state == State.consumed:
            raise HTTPException(status_code=409, detail="transcription_result_expired")
        existing.state = State.reserved
        existing.cost = estimated_minutes
        session.add(existing)
        await session.commit()
        return existing

    ledger = RequestLedger(
        user_id=user_id,
        tier_id=tier_id,
        request_id=request_id,
        model_name=model,
        feature="transcription",
        cost=estimated_minutes,
        state=State.reserved,
    )
    session.add(ledger)
    try:
        await session.commit()
        return ledger
    except IntegrityError:
        await session.rollback()
        existing = (
            await session.exec(
                select(RequestLedger).where(
                    RequestLedger.user_id == user_id,
                    RequestLedger.request_id == request_id,
                )
            )
        ).first()
        if existing and existing.feature == "transcription":
            raise HTTPException(status_code=409, detail="transcription_in_progress")
        raise HTTPException(status_code=409, detail="request_id_conflict")


def _usage_row(
    *,
    user_id: uuid.UUID,
    request_id: str,
    model: str,
    result: TranscriptionResult | None,
    duration_seconds: float,
    status_value: str,
    error_message: str | None = None,
) -> TokenUsage:
    total_cost = Decimal(settings.VOICE_TRANSCRIPTION_COST_PER_MINUTE_USD) * (
        Decimal(str(duration_seconds)) / Decimal("60")
    )
    return TokenUsage(
        user_id=user_id,
        provider="openai",
        model_name=model,
        request_id=request_id,
        status=status_value,
        error_message=error_message,
        input_tokens=result.input_tokens if result else 0,
        output_tokens=result.output_tokens if result else 0,
        total_cost=total_cost,
    )


@audio.get("/transcriptions/limits", response_model=AudioTranscriptionLimits)
async def get_audio_transcription_limits(
    current_user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> AudioTranscriptionLimits:
    if not settings.VOICE_TRANSCRIPTION_ENABLED:
        raise HTTPException(status_code=404, detail="voice_transcription_disabled")
    try:
        ensure_audio_storage_configured()
    except PrivateAudioStorageConfigurationError as exc:
        raise HTTPException(status_code=503, detail="voice_transcription_storage_unavailable") from exc
    if not await redis.get(f"voice:transcription:worker:{settings.DEPLOYMENT_CHANNEL}"):
        raise HTTPException(status_code=503, detail="voice_transcription_worker_unavailable")
    tier = await _get_transcription_tier(session, current_user.id)
    remaining = 0.0
    if tier:
        used = await _used_transcription_minutes(
            session, user_id=current_user.id, tier_id=tier.id, exclude_request_id=""
        )
        remaining = max(0.0, tier.monthly_transcription_minutes - used)
    return AudioTranscriptionLimits(
        entitled=tier is not None,
        max_bytes=settings.VOICE_TRANSCRIPTION_UPLOAD_MAX_BYTES,
        max_duration_seconds=settings.VOICE_TRANSCRIPTION_UPLOAD_MAX_DURATION_SECONDS,
        remaining_minutes=remaining,
        allowed_extensions=_ALLOWED_EXTENSIONS,
        recording_max_bytes=settings.VOICE_TRANSCRIPTION_MAX_BYTES,
        recording_max_duration_seconds=settings.VOICE_TRANSCRIPTION_MAX_DURATION_SECONDS,
    )


async def _upload_job(
    *, audio_file: UploadFile, client_request_id: uuid.UUID,
    current_user: AppUser, session: AsyncSession, redis: Redis, response: Response,
) -> AudioTranscriptionJobResponse:
    request_id = str(client_request_id)
    existing = (await session.exec(select(AudioTranscriptionJob).where(
        AudioTranscriptionJob.user_id == current_user.id,
        AudioTranscriptionJob.request_id == client_request_id,
        AudioTranscriptionJob.channel == settings.DEPLOYMENT_CHANNEL,
    ))).first()
    if existing:
        response.status_code = 202 if existing.status in _UPLOAD_PENDING else 200
        if existing.status in _UPLOAD_PENDING:
            response.headers["Retry-After"] = "2"
        return job_response(existing)

    tier = await _get_transcription_tier(session, current_user.id)
    if not tier:
        raise HTTPException(status_code=403, detail="voice_transcription_subscription_required")
    await _rate_limit(redis, current_user.id)
    contents = await audio_file.read(settings.VOICE_TRANSCRIPTION_UPLOAD_MAX_BYTES + 1)
    await audio_file.close()
    if not contents:
        raise HTTPException(status_code=422, detail="voice_transcription_empty_audio")
    if len(contents) > settings.VOICE_TRANSCRIPTION_UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=413, detail="voice_transcription_too_large")
    try:
        probed = await to_thread(probe_audio, contents)
    except InvalidAudioError as exc:
        raise HTTPException(status_code=415, detail="voice_transcription_format_unsupported") from exc
    if probed.duration_seconds > settings.VOICE_TRANSCRIPTION_UPLOAD_MAX_DURATION_SECONDS + 1:
        raise HTTPException(status_code=413, detail="voice_transcription_too_long")
    try:
        ensure_audio_storage_configured()
    except PrivateAudioStorageConfigurationError as exc:
        raise HTTPException(status_code=503, detail="voice_transcription_storage_unavailable") from exc
    if not await redis.get(f"voice:transcription:worker:{settings.DEPLOYMENT_CHANNEL}"):
        raise HTTPException(status_code=503, detail="voice_transcription_worker_unavailable")

    job_id = uuid.uuid4()
    key = audio_key(settings.DEPLOYMENT_CHANNEL, current_user.id, job_id, probed.extension)
    try:
        # An AppUser row lock serializes quota admission across production and
        # beta, whose Redis instances are intentionally separate.
        locked_user = (await session.exec(select(AppUser).where(
            AppUser.id == current_user.id,
        ).with_for_update().execution_options(populate_existing=True))).one()
        if locked_user.deleted_at is not None:
            raise HTTPException(status_code=403, detail="account_deleted")
        existing = (await session.exec(select(AudioTranscriptionJob).where(
            AudioTranscriptionJob.user_id == current_user.id,
            AudioTranscriptionJob.request_id == client_request_id,
        ))).first()
        if existing:
            if existing.channel != settings.DEPLOYMENT_CHANNEL:
                raise HTTPException(status_code=409, detail="request_id_conflict")
            existing_response = job_response(existing)
            existing_pending = existing.status in _UPLOAD_PENDING
            await session.rollback()
            response.status_code = 202 if existing_pending else 200
            if existing_pending:
                response.headers["Retry-After"] = "2"
            return existing_response
        prior_ledger = (await session.exec(select(RequestLedger).where(
            RequestLedger.user_id == current_user.id,
            RequestLedger.request_id == request_id,
        ))).first()
        if prior_ledger:
            raise HTTPException(status_code=409, detail="transcription_result_expired")
        pending = (await session.exec(select(func.count()).select_from(AudioTranscriptionJob).where(
            AudioTranscriptionJob.user_id == current_user.id,
            AudioTranscriptionJob.status.in_(_UPLOAD_PENDING),
        ))).one()
        if pending >= settings.VOICE_TRANSCRIPTION_UPLOAD_MAX_PENDING_PER_USER:
            raise HTTPException(status_code=429, detail="voice_transcription_too_many_pending")
        await session.exec(sql_text("SELECT pg_advisory_xact_lock(:key)"), params={"key": 482091735})
        queued = (await session.exec(select(func.count()).select_from(AudioTranscriptionJob).where(
            AudioTranscriptionJob.status.in_(_UPLOAD_PENDING),
        ))).one()
        if queued >= settings.VOICE_TRANSCRIPTION_UPLOAD_MAX_QUEUE:
            raise HTTPException(status_code=503, detail="voice_transcription_queue_full")
        used = await _used_transcription_minutes(
            session, user_id=current_user.id, tier_id=tier.id, exclude_request_id=request_id,
        )
        requested_minutes = probed.duration_seconds / 60
        if used + requested_minutes > tier.monthly_transcription_minutes:
            raise HTTPException(status_code=429, detail="voice_transcription_allowance_exhausted")
        now = datetime.now(UTC).replace(tzinfo=None)
        session.add(RequestLedger(
            user_id=current_user.id, tier_id=tier.id, request_id=request_id,
            model_name=settings.VOICE_TRANSCRIPTION_MODEL, feature="transcription",
            cost=requested_minutes, state=State.reserved,
        ))
        job = AudioTranscriptionJob(
            id=job_id, user_id=current_user.id, request_id=client_request_id,
            channel=settings.DEPLOYMENT_CHANNEL, status="storing",
            model_name=settings.VOICE_TRANSCRIPTION_MODEL,
            audio_key=key, audio_extension=probed.extension,
            duration_seconds=probed.duration_seconds,
            created_at=now, updated_at=now, expires_at=now + timedelta(hours=24),
        )
        session.add(job)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        existing = (await session.exec(select(AudioTranscriptionJob).where(
            AudioTranscriptionJob.user_id == current_user.id,
            AudioTranscriptionJob.request_id == client_request_id,
            AudioTranscriptionJob.channel == settings.DEPLOYMENT_CHANNEL,
        ))).first()
        if not existing:
            raise HTTPException(status_code=409, detail="request_id_conflict") from exc
        response.status_code = 202 if existing.status in _UPLOAD_PENDING else 200
        if existing.status in _UPLOAD_PENDING:
            response.headers["Retry-After"] = "2"
        return job_response(existing)

    # The durable storing row exists before any private object write. A crash
    # anywhere below leaves a key that maintenance can delete.
    try:
        await upload_audio(key, contents)
    except Exception as exc:
        logger.exception("Could not store transcription input job_id=%s", job_id)
        # A failed/partial PUT can race account deletion. Keep the deletion
        # tombstone and its key until the sweeper has had time to remove any
        # late object; never resurrect a deleted account's job.
        locked_user = (await session.exec(select(AppUser).where(
            AppUser.id == current_user.id,
        ).with_for_update().execution_options(populate_existing=True))).first()
        current_job = (await session.exec(select(AudioTranscriptionJob).where(
            AudioTranscriptionJob.id == job_id,
        ).with_for_update().execution_options(populate_existing=True))).first()
        if current_job and current_job.status == "storing" and locked_user and locked_user.deleted_at is None:
            current_job.status = "failed"
            current_job.error_code = "voice_transcription_storage_unavailable"
            current_job.updated_at = datetime.now(UTC).replace(tzinfo=None)
            current_job.expires_at = current_job.updated_at + timedelta(hours=24)
            ledger = (await session.exec(select(RequestLedger).where(
                RequestLedger.user_id == current_user.id,
                RequestLedger.request_id == request_id,
            ).with_for_update())).first()
            if ledger and ledger.state == State.reserved:
                ledger.state = State.failed
                session.add(ledger)
            session.add(current_job)
            await session.commit()
        else:
            await session.rollback()
        try:
            await delete_audio(key)
            if current_job and current_job.status == "failed":
                current_job.audio_key = None
                session.add(current_job)
                await session.commit()
        except Exception:
            logger.exception("Could not delete failed transcription input job_id=%s", job_id)
        raise HTTPException(status_code=503, detail="voice_transcription_storage_unavailable") from exc
    finally:
        del contents

    # Account deletion can run while the object PUT is in flight. Both paths
    # lock the user before the job so deletion cannot miss a late write.
    locked_user = (await session.exec(select(AppUser).where(
        AppUser.id == current_user.id,
    ).with_for_update().execution_options(populate_existing=True))).first()
    current_job = (await session.exec(select(AudioTranscriptionJob).where(
        AudioTranscriptionJob.id == job_id,
    ).with_for_update().execution_options(populate_existing=True))).first()
    if locked_user is None or locked_user.deleted_at is not None or current_job is None or current_job.status != "storing":
        await session.rollback()
        try:
            await delete_audio(key)
        except Exception:
            logger.exception("Could not delete cancelled transcription input job_id=%s", job_id)
        raise HTTPException(status_code=409, detail="voice_transcription_account_deleted")
    current_job.status = "queued"
    current_job.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(current_job)
    await session.commit()
    response.status_code = 202
    response.headers["Retry-After"] = "2"
    return job_response(current_job)


@audio.get("/transcriptions/{request_id}", response_model=AudioTranscriptionJobResponse)
async def get_audio_transcription_job(
    request_id: uuid.UUID,
    response: Response,
    current_user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AudioTranscriptionJobResponse:
    if not settings.VOICE_TRANSCRIPTION_ENABLED:
        raise HTTPException(status_code=404, detail="voice_transcription_disabled")
    job = (await session.exec(select(AudioTranscriptionJob).where(
        AudioTranscriptionJob.user_id == current_user.id,
        AudioTranscriptionJob.request_id == request_id,
        AudioTranscriptionJob.channel == settings.DEPLOYMENT_CHANNEL,
    ))).first()
    if job is None or job.expires_at < datetime.now(UTC).replace(tzinfo=None):
        raise HTTPException(status_code=404, detail="transcription_result_expired")
    if job.status in _UPLOAD_PENDING:
        response.headers["Retry-After"] = "2"
    return job_response(job)


@audio.post("/transcriptions", response_model=AudioTranscriptionResponse | AudioTranscriptionJobResponse)
async def create_audio_transcription(
    response: Response,
    audio_file: UploadFile = File(alias="audio"),
    client_request_id: uuid.UUID = Form(),
    duration_ms: int | None = Form(default=None, gt=0),
    source: Literal["recording", "upload"] = Form(default="recording"),
    current_user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> AudioTranscriptionResponse | AudioTranscriptionJobResponse:
    if not settings.VOICE_TRANSCRIPTION_ENABLED:
        raise HTTPException(status_code=404, detail="voice_transcription_disabled")

    if source == "upload":
        return await _upload_job(
            audio_file=audio_file, client_request_id=client_request_id,
            current_user=current_user, session=session, redis=redis, response=response,
        )

    tier = await _get_transcription_tier(session, current_user.id)
    if not tier:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="voice_transcription_subscription_required",
        )

    request_id = str(client_request_id)
    cached = await _cached_response(redis, current_user.id, request_id)
    if cached:
        return cached

    await _rate_limit(redis, current_user.id)
    contents = await audio_file.read(settings.VOICE_TRANSCRIPTION_MAX_BYTES + 1)
    await audio_file.close()
    if not contents:
        raise HTTPException(status_code=422, detail="voice_transcription_empty_audio")
    if len(contents) > settings.VOICE_TRANSCRIPTION_MAX_BYTES:
        raise HTTPException(status_code=413, detail="voice_transcription_too_large")
    try:
        probed = await to_thread(probe_audio, contents)
    except InvalidAudioError as exc:
        raise HTTPException(
            status_code=415, detail="voice_transcription_format_unsupported"
        ) from exc
    if probed.duration_seconds > settings.VOICE_TRANSCRIPTION_MAX_DURATION_SECONDS + 1:
        raise HTTPException(status_code=413, detail="voice_transcription_too_long")

    lock_key = _lock_key(current_user.id, request_id)
    lock_token = uuid.uuid4().hex
    locked = await redis.set(
        lock_key,
        lock_token,
        ex=max(120, int(settings.VOICE_TRANSCRIPTION_TIMEOUT_SECONDS * 2)),
        nx=True,
    )
    if not locked:
        cached = await _cached_response(redis, current_user.id, request_id)
        if cached:
            return cached
        raise HTTPException(status_code=409, detail="transcription_in_progress")

    estimated_seconds = probed.duration_seconds
    ledger: RequestLedger | None = None
    try:
        quota_lock_key = _quota_lock_key(current_user.id)
        quota_lock_token = uuid.uuid4().hex
        quota_locked = await redis.set(quota_lock_key, quota_lock_token, ex=30, nx=True)
        if not quota_locked:
            raise HTTPException(status_code=409, detail="transcription_in_progress")
        try:
            # Upload and recording consume one monthly bucket, including when
            # production and beta use different Redis instances.
            locked_user = (await session.exec(select(AppUser).where(
                AppUser.id == current_user.id,
            ).with_for_update().execution_options(populate_existing=True))).one()
            if locked_user.deleted_at is not None:
                raise HTTPException(status_code=403, detail="account_deleted")
            uploaded_job = (await session.exec(select(AudioTranscriptionJob.id).where(
                AudioTranscriptionJob.user_id == current_user.id,
                AudioTranscriptionJob.request_id == client_request_id,
            ))).first()
            if uploaded_job is not None:
                raise HTTPException(status_code=409, detail="request_id_conflict")
            estimated_minutes = estimated_seconds / 60
            used_minutes = await _used_transcription_minutes(
                session,
                user_id=current_user.id,
                tier_id=tier.id,
                exclude_request_id=request_id,
            )
            if used_minutes + estimated_minutes > tier.monthly_transcription_minutes:
                raise HTTPException(
                    status_code=429,
                    detail="voice_transcription_allowance_exhausted",
                )
            ledger = await _reserve_ledger(
                session,
                user_id=current_user.id,
                tier_id=tier.id,
                request_id=request_id,
                model=settings.VOICE_TRANSCRIPTION_MODEL,
                estimated_minutes=estimated_minutes,
            )
        finally:
            await _release_lock(redis, quota_lock_key, quota_lock_token)
        result = await transcribe_audio(
            audio=contents,
            filename=f"voice{probed.extension}",
            model=settings.VOICE_TRANSCRIPTION_MODEL,
        )
        if not result.text:
            raise HTTPException(status_code=422, detail="voice_transcription_no_speech")

        duration_seconds = estimated_seconds
        ledger.state = State.consumed
        ledger.cost = duration_seconds / 60
        session.add(ledger)
        session.add(
            _usage_row(
                user_id=current_user.id,
                request_id=request_id,
                model=settings.VOICE_TRANSCRIPTION_MODEL,
                result=result,
                duration_seconds=duration_seconds,
                status_value="success",
            )
        )
        await session.commit()

        response = AudioTranscriptionResponse(
            request_id=request_id,
            text=result.text,
            model=settings.VOICE_TRANSCRIPTION_MODEL,
            duration_seconds=duration_seconds,
        )
        try:
            await redis.set(
                _cache_key(current_user.id, request_id),
                response.model_dump_json(),
                ex=settings.VOICE_TRANSCRIPTION_RESULT_TTL_SECONDS,
            )
        except Exception:
            logger.exception("Transcription result cache failed request_id=%s", request_id)
        return response
    except HTTPException:
        if ledger and ledger.state == State.reserved:
            ledger.state = State.failed
            session.add(ledger)
            await session.commit()
        raise
    except (
        AuthenticationError,
        APITimeoutError,
        APIConnectionError,
        APIStatusError,
    ) as exc:
        logger.warning(
            "Voice transcription provider error request_id=%s type=%s",
            request_id,
            type(exc).__name__,
        )
        if ledger:
            ledger.state = State.failed
            session.add(ledger)
            session.add(
                _usage_row(
                    user_id=current_user.id,
                    request_id=request_id,
                    model=settings.VOICE_TRANSCRIPTION_MODEL,
                    result=None,
                    duration_seconds=0,
                    status_value="error",
                    error_message=type(exc).__name__,
                )
            )
            await session.commit()
        if isinstance(exc, APIStatusError) and exc.status_code in {400, 413, 415, 422}:
            raise HTTPException(
                status_code=422, detail="voice_transcription_audio_invalid"
            ) from exc
        raise HTTPException(
            status_code=502, detail="voice_transcription_provider_unavailable"
        ) from exc
    except Exception as exc:
        logger.exception(
            "Voice transcription failed request_id=%s type=%s",
            request_id,
            type(exc).__name__,
        )
        if ledger:
            await session.rollback()
            ledger.state = State.failed
            session.add(ledger)
            await session.commit()
        raise HTTPException(
            status_code=500, detail="voice_transcription_failed"
        ) from exc
    finally:
        await _release_lock(redis, lock_key, lock_token)
