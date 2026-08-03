import json
import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
)
from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.dependencies import get_current_user, get_redis
from app.core.config import settings
from app.db.database import get_session
from app.db.models import AppUser, RequestLedger, State, TokenUsage
from app.db.subscription_tiers import (
    SubscriptionStatus,
    SubscriptionTier,
    UserSubscription,
)
from app.schemas.audio import AudioTranscriptionResponse
from app.services.transcription_service import TranscriptionResult, transcribe_audio


audio = APIRouter(tags=["audio"], prefix="/audio")
logger = logging.getLogger(__name__)

_ALLOWED_AUDIO_TYPES = {
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/webm": ".webm",
    "audio/x-m4a": ".m4a",
}


def _cache_key(user_id: uuid.UUID, request_id: str) -> str:
    return f"voice:transcription:result:{user_id}:{request_id}"


def _lock_key(user_id: uuid.UUID, request_id: str) -> str:
    return f"voice:transcription:lock:{user_id}:{request_id}"


def _quota_lock_key(user_id: uuid.UUID) -> str:
    return f"voice:transcription:quota-lock:{user_id}"


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


@audio.post("/transcriptions", response_model=AudioTranscriptionResponse)
async def create_audio_transcription(
    audio_file: UploadFile = File(alias="audio"),
    client_request_id: uuid.UUID = Form(),
    duration_ms: int = Form(gt=0),
    current_user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> AudioTranscriptionResponse:
    if not settings.VOICE_TRANSCRIPTION_ENABLED:
        raise HTTPException(status_code=404, detail="voice_transcription_disabled")

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

    max_duration_ms = settings.VOICE_TRANSCRIPTION_MAX_DURATION_SECONDS * 1000
    if duration_ms > max_duration_ms:
        raise HTTPException(status_code=413, detail="voice_transcription_too_long")

    content_type = (audio_file.content_type or "").split(";", 1)[0].lower()
    extension = _ALLOWED_AUDIO_TYPES.get(content_type)
    if not extension:
        raise HTTPException(
            status_code=415, detail="voice_transcription_format_unsupported"
        )

    contents = await audio_file.read(settings.VOICE_TRANSCRIPTION_MAX_BYTES + 1)
    await audio_file.close()
    if not contents:
        raise HTTPException(status_code=422, detail="voice_transcription_empty_audio")
    if len(contents) > settings.VOICE_TRANSCRIPTION_MAX_BYTES:
        raise HTTPException(status_code=413, detail="voice_transcription_too_large")

    await _rate_limit(redis, current_user.id)
    lock_key = _lock_key(current_user.id, request_id)
    locked = await redis.set(
        lock_key,
        "1",
        ex=max(120, int(settings.VOICE_TRANSCRIPTION_TIMEOUT_SECONDS * 2)),
        nx=True,
    )
    if not locked:
        cached = await _cached_response(redis, current_user.id, request_id)
        if cached:
            return cached
        raise HTTPException(status_code=409, detail="transcription_in_progress")

    estimated_seconds = duration_ms / 1000
    ledger: RequestLedger | None = None
    try:
        quota_lock_key = _quota_lock_key(current_user.id)
        quota_locked = await redis.set(quota_lock_key, "1", ex=30, nx=True)
        if not quota_locked:
            raise HTTPException(status_code=409, detail="transcription_in_progress")
        try:
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
            await redis.delete(quota_lock_key)
        result = await transcribe_audio(
            audio=contents,
            filename=f"voice{extension}",
            model=settings.VOICE_TRANSCRIPTION_MODEL,
        )
        if not result.text:
            raise HTTPException(status_code=422, detail="voice_transcription_no_speech")

        duration_seconds = result.duration_seconds or estimated_seconds
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
        await redis.set(
            _cache_key(current_user.id, request_id),
            response.model_dump_json(),
            ex=settings.VOICE_TRANSCRIPTION_RESULT_TTL_SECONDS,
        )
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
        await redis.delete(lock_key)
