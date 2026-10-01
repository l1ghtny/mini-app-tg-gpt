import os
import asyncio
import subprocess
import uuid
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Response, UploadFile
from starlette.datastructures import Headers
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api import audio as audio_api
from app.api.dependencies import get_current_user, get_redis
from app.core.config import settings
from app.db.database import get_session
from app.db.models import AppUser, AudioTranscriptionJob, RequestLedger, State, TokenUsage
from app.db.subscription_tiers import SubscriptionTier, UserSubscription
from app.services.transcription_service import TranscriptionResult
from app.services import transcription_service
from app.services import audio_jobs
from app.services.audio_probe import InvalidAudioError, probe_audio


class FakeRedis:
    def __init__(self):
        self.values: dict[str, str | int] = {}

    async def get(self, key: str):
        return self.values.get(key)

    async def set(self, key: str, value, *, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def incr(self, key: str):
        value = int(self.values.get(key, 0)) + 1
        self.values[key] = value
        return value

    async def expire(self, key: str, seconds: int):
        return key in self.values

    async def delete(self, key: str):
        self.values.pop(key, None)
        return 1

    async def eval(self, _script, _number_of_keys, key, token):
        if self.values.get(key) == token:
            await self.delete(key)
            return 1
        return 0


@pytest.fixture(scope="module")
def audio_samples(tmp_path_factory):
    samples = {}
    sample_dir = tmp_path_factory.mktemp("transcription-audio")
    for extension, codec in (
        ("webm", "libopus"), ("mp3", "libmp3lame"),
        ("m4a", "aac"), ("wav", "pcm_s16le"),
    ):
        target = sample_dir / f"sample.{extension}"
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                "sine=frequency=440:duration=2.5", "-c:a", codec,
                str(target),
            ],
            capture_output=True, check=True,
        )
        samples[extension] = target.read_bytes()
    return samples


def test_probe_identifies_actual_audio_and_duration(audio_samples):
    for extension, contents in audio_samples.items():
        probed = probe_audio(contents)
        assert probed.extension == f".{extension}"
        assert probed.duration_seconds == pytest.approx(2.5, abs=0.15)

    with pytest.raises(InvalidAudioError):
        probe_audio(b"plain text impersonating audio")

    # Non-seekable WebM output has no container duration, as with MediaRecorder.
    live_webm = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=2.5", "-c:a", "libopus",
         "-f", "webm", "pipe:1"],
        capture_output=True, check=True,
    ).stdout
    assert probe_audio(live_webm).duration_seconds == pytest.approx(2.5, abs=0.15)


@pytest.mark.asyncio
async def test_uploaded_audio_uses_probe_for_quota_and_settlement(monkeypatch, audio_samples):
    user = SimpleNamespace(id=uuid.uuid4())
    tier = SimpleNamespace(id=uuid.uuid4(), monthly_transcription_minutes=1)
    redis = FakeRedis()
    ledger = SimpleNamespace(state=State.reserved, cost=0)
    class LockResult:
        def one(self):
            return SimpleNamespace(deleted_at=None)

        def first(self):
            return None

    async def exec_lock(_statement):
        return LockResult()

    session = SimpleNamespace(add=lambda item: None, commit=_noop, exec=exec_lock)
    provider_calls = 0

    async def reserve(*_args, **kwargs):
        ledger.state = State.reserved
        ledger.cost = kwargs["estimated_minutes"]
        return ledger

    async def transcribe(**kwargs):
        nonlocal provider_calls
        provider_calls += 1
        assert kwargs["filename"] == "voice.mp3"
        return TranscriptionResult("Hello", 0.1, 1, 1)

    monkeypatch.setattr(settings, "VOICE_TRANSCRIPTION_ENABLED", True)
    monkeypatch.setattr(audio_api, "_get_transcription_tier", lambda *_: _return(tier))
    monkeypatch.setattr(audio_api, "_used_transcription_minutes", lambda *_a, **_k: _return(0))
    monkeypatch.setattr(audio_api, "_reserve_ledger", reserve)
    monkeypatch.setattr(audio_api, "transcribe_audio", transcribe)
    request_id = uuid.uuid4()
    async def call(request_id_value=request_id):
        return await audio_api.create_audio_transcription(
            response=Response(),
            audio_file=UploadFile(
                file=BytesIO(audio_samples["mp3"]), filename="spoofed.webm",
                headers=Headers({"content-type": "audio/x-wav"}),
            ),
            client_request_id=request_id_value, duration_ms=1, source="recording",
            current_user=user, session=session, redis=redis,
        )

    first = await call()
    second = await call()
    assert first.duration_seconds == pytest.approx(2.5, abs=0.15)
    assert ledger.cost == pytest.approx(first.duration_seconds / 60)
    assert second.cached is True
    assert provider_calls == 1

    monkeypatch.setattr(audio_api, "_used_transcription_minutes", lambda *_a, **_k: _return(0.99))
    with pytest.raises(HTTPException) as exhausted:
        await call(uuid.uuid4())
    assert exhausted.value.detail == "voice_transcription_allowance_exhausted"
    assert provider_calls == 1

    monkeypatch.setattr(audio_api, "_used_transcription_minutes", lambda *_a, **_k: _return(0))
    ordinary_set = redis.set

    async def failing_cache_set(key, value, **kwargs):
        if key.startswith("voice:transcription:result:"):
            raise ConnectionError("cache unavailable")
        return await ordinary_set(key, value, **kwargs)

    monkeypatch.setattr(redis, "set", failing_cache_set)
    uncached = await call(uuid.uuid4())
    assert uncached.text == "Hello"
    assert ledger.state == State.consumed
    assert provider_calls == 2

    async def no_speech(**_kwargs):
        return TranscriptionResult("", 2.5, 0, 0)

    monkeypatch.setattr(audio_api, "transcribe_audio", no_speech)
    with pytest.raises(HTTPException) as silent:
        await call(uuid.uuid4())
    assert silent.value.detail == "voice_transcription_no_speech"
    assert ledger.state == State.failed


async def _noop():
    return None


async def _return(value):
    return value


@pytest.mark.asyncio
async def test_invalid_and_oversize_audio_are_rejected_before_reservation(
    monkeypatch, audio_samples
):
    user = SimpleNamespace(id=uuid.uuid4())
    redis = FakeRedis()
    session = SimpleNamespace()
    monkeypatch.setattr(settings, "VOICE_TRANSCRIPTION_ENABLED", True)
    monkeypatch.setattr(audio_api, "_get_transcription_tier", lambda *_: _return(
        SimpleNamespace(id=uuid.uuid4(), monthly_transcription_minutes=10)
    ))

    async def call(contents):
        return await audio_api.create_audio_transcription(
            response=Response(),
            audio_file=UploadFile(file=BytesIO(contents), filename="sound.mp3"),
            client_request_id=uuid.uuid4(), duration_ms=None, source="recording",
            current_user=user, session=session, redis=redis,
        )

    with pytest.raises(HTTPException) as invalid:
        await call(b"not-audio")
    assert invalid.value.detail == "voice_transcription_format_unsupported"
    monkeypatch.setattr(settings, "VOICE_TRANSCRIPTION_MAX_BYTES", 3)
    with pytest.raises(HTTPException) as large:
        await call(audio_samples["mp3"])
    assert large.value.detail == "voice_transcription_too_large"
    monkeypatch.setattr(settings, "VOICE_TRANSCRIPTION_MAX_BYTES", 10 * 1024 * 1024)
    monkeypatch.setattr(settings, "VOICE_TRANSCRIPTION_MAX_DURATION_SECONDS", 1)
    with pytest.raises(HTTPException) as long:
        await call(audio_samples["mp3"])
    assert long.value.detail == "voice_transcription_too_long"


@pytest.mark.asyncio
async def test_limits_show_zero_without_entitled_tier(monkeypatch):
    monkeypatch.setattr(settings, "VOICE_TRANSCRIPTION_ENABLED", True)
    monkeypatch.setattr(audio_api, "_get_transcription_tier", lambda *_: _return(None))
    monkeypatch.setattr(audio_api, "ensure_audio_storage_configured", lambda: None)
    redis = FakeRedis()
    redis.values[f"voice:transcription:worker:{settings.DEPLOYMENT_CHANNEL}"] = "1"
    limits = await audio_api.get_audio_transcription_limits(
        current_user=SimpleNamespace(id=uuid.uuid4()), session=SimpleNamespace(), redis=redis,
    )
    assert limits.remaining_minutes == 0
    assert limits.monthly_limit_minutes == 0
    assert limits.resets_at is None
    assert limits.upload_available is True
    assert limits.entitled is False
    assert limits.allowed_extensions == ["mp3", "m4a", "wav", "webm"]
    assert limits.max_duration_seconds == 1800
    assert limits.max_bytes == 20 * 1024 * 1024
    assert limits.recording_max_duration_seconds == 300


def test_next_utc_calendar_month_handles_year_and_leap_day():
    assert audio_api._next_utc_month_start(datetime(2026, 12, 31, 23, tzinfo=UTC)) == datetime(
        2027, 1, 1, tzinfo=UTC
    )
    assert audio_api._next_utc_month_start(datetime(2028, 2, 29, 23, tzinfo=UTC)) == datetime(
        2028, 3, 1, tzinfo=UTC
    )
    assert audio_api._utc_month_start(datetime(2028, 2, 29, 23, tzinfo=UTC)) == datetime(
        2028, 2, 1
    )


@pytest.mark.asyncio
async def test_openai_transcription_adapter_sends_named_audio(monkeypatch):
    captured = {}

    class FakeTranscriptions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                text="  Spoken message  ",
                usage=SimpleNamespace(seconds=3.25, input_tokens=9, output_tokens=3),
            )

    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=FakeTranscriptions()))
    client.with_options = lambda **kwargs: client
    monkeypatch.setattr(
        transcription_service,
        "_client",
        client,
    )

    result = await transcription_service.transcribe_audio(
        audio=b"audio",
        filename="voice.webm",
        model="gpt-transcribe",
    )

    assert captured["file"].name == "voice.webm"
    assert captured["file"].getvalue() == b"audio"
    assert captured["model"] == "gpt-transcribe"
    assert captured["response_format"] == "json"
    assert result == TranscriptionResult(
        text="Spoken message",
        duration_seconds=3.25,
        input_tokens=9,
        output_tokens=3,
    )


async def _create_user(engine, *, tier_name: str | None) -> AppUser:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=730000000 + (1 if tier_name else 2))
        session.add(user)
        await session.flush()
        if tier_name:
            if tier_name == "Smooth tier":
                session.add(
                    SubscriptionTier(
                        name=tier_name,
                        name_ru="Гладкая подписка",
                        price_cents=0,
                        monthly_transcription_minutes=120,
                        is_active=True,
                        is_public=False,
                    )
                )
                await session.flush()
            tier = (
                await session.exec(
                    select(SubscriptionTier).where(SubscriptionTier.name == tier_name)
                )
            ).one()
            if tier_name == "advanced":
                # The production test seed predates transcription and grants
                # this legacy tier zero minutes. Give this synthetic test user
                # an explicit allowance without changing production defaults.
                tier.monthly_transcription_minutes = 180
                session.add(tier)
            session.add(UserSubscription(user_id=user.id, tier_id=tier.id))
        await session.commit()
        return user


def _build_app(engine, user: AppUser, redis: FakeRedis) -> FastAPI:
    app = FastAPI()
    app.include_router(audio_api.audio, prefix="/api/v1")

    async def _fake_get_session():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_session] = _fake_get_session
    app.dependency_overrides[get_redis] = lambda: redis
    return app


@pytest.mark.asyncio
async def test_transcription_requires_a_tier_allowance(monkeypatch, audio_samples):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)
    user = await _create_user(engine, tier_name=None)
    redis = FakeRedis()
    redis.values[f"voice:transcription:worker:{settings.DEPLOYMENT_CHANNEL}"] = "1"
    app = _build_app(engine, user, redis)
    monkeypatch.setattr(settings, "VOICE_TRANSCRIPTION_ENABLED", True)
    monkeypatch.setattr(audio_api, "ensure_audio_storage_configured", lambda: None)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        limits = await client.get("/api/v1/audio/transcriptions/limits")
        response = await client.post(
            "/api/v1/audio/transcriptions",
            data={"client_request_id": str(uuid.uuid4()), "duration_ms": "1000"},
            files={"audio": ("voice.webm", audio_samples["webm"], "audio/webm")},
        )

    assert limits.status_code == 200
    assert limits.json()["entitled"] is False
    assert limits.json()["remaining_minutes"] == 0
    assert limits.json()["monthly_limit_minutes"] == 0
    assert limits.json()["resets_at"] is None
    assert limits.json()["upload_available"] is True
    assert response.status_code == 403
    assert response.json()["detail"] == "voice_transcription_subscription_required"
    await engine.dispose()


@pytest.mark.asyncio
async def test_transcription_is_idempotent_and_records_usage(monkeypatch, audio_samples):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)
    user = await _create_user(engine, tier_name="advanced")
    redis = FakeRedis()
    app = _build_app(engine, user, redis)
    request_id = str(uuid.uuid4())
    provider_calls = 0

    async def _fake_transcribe_audio(**kwargs):
        nonlocal provider_calls
        provider_calls += 1
        assert kwargs["audio"] == audio_samples["webm"]
        assert kwargs["filename"] == "voice.webm"
        return TranscriptionResult(
            text="Editable transcript",
            duration_seconds=2.5,
            input_tokens=12,
            output_tokens=4,
        )

    monkeypatch.setattr(settings, "VOICE_TRANSCRIPTION_ENABLED", True)
    monkeypatch.setattr(audio_api, "transcribe_audio", _fake_transcribe_audio)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post(
            "/api/v1/audio/transcriptions",
            data={"client_request_id": request_id, "duration_ms": "2400"},
            files={"audio": ("voice.webm", audio_samples["webm"], "audio/webm")},
        )
        second = await client.post(
            "/api/v1/audio/transcriptions",
            data={"client_request_id": request_id, "duration_ms": "2400"},
            files={"audio": ("voice.webm", audio_samples["webm"], "audio/webm")},
        )

    assert first.status_code == 200
    assert first.json()["text"] == "Editable transcript"
    assert first.json()["cached"] is False
    assert second.status_code == 200
    assert second.json()["cached"] is True
    assert provider_calls == 1

    async with AsyncSession(engine, expire_on_commit=False) as session:
        ledger = (
            await session.exec(
                select(RequestLedger).where(
                    RequestLedger.user_id == user.id,
                    RequestLedger.request_id == request_id,
                )
            )
        ).one()
        usage = (
            await session.exec(
                select(TokenUsage).where(TokenUsage.request_id == request_id)
            )
        ).one()
    assert ledger.feature == "transcription"
    assert ledger.state == State.consumed
    assert ledger.cost == pytest.approx(probe_audio(audio_samples["webm"]).duration_seconds / 60)
    assert usage.input_tokens == 12
    assert usage.output_tokens == 4
    assert usage.total_cost > 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_zero_price_smooth_tier_receives_beta_allowance(monkeypatch, audio_samples):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)
    user = await _create_user(engine, tier_name="Smooth tier")
    redis = FakeRedis()
    redis.values[f"voice:transcription:worker:{settings.DEPLOYMENT_CHANNEL}"] = "1"
    app = _build_app(engine, user, redis)

    async def _fake_transcribe_audio(**_kwargs):
        return TranscriptionResult(
            text="Smooth transcript",
            duration_seconds=1.5,
            input_tokens=6,
            output_tokens=2,
        )

    monkeypatch.setattr(settings, "VOICE_TRANSCRIPTION_ENABLED", True)
    monkeypatch.setattr(audio_api, "ensure_audio_storage_configured", lambda: None)
    monkeypatch.setattr(audio_api, "transcribe_audio", _fake_transcribe_audio)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        limits = await client.get("/api/v1/audio/transcriptions/limits")
        response = await client.post(
            "/api/v1/audio/transcriptions",
            data={"client_request_id": str(uuid.uuid4()), "duration_ms": "1500"},
            files={"audio": ("voice.webm", audio_samples["webm"], "audio/webm")},
        )

    assert limits.status_code == 200
    assert limits.json()["entitled"] is True
    assert limits.json()["remaining_minutes"] == 120
    assert response.status_code == 200
    assert response.json()["text"] == "Smooth transcript"
    await engine.dispose()


@pytest.mark.asyncio
async def test_limits_keep_quota_visible_when_upload_unavailable(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)
    user = await _create_user(engine, tier_name="advanced")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        tier = (await session.exec(select(SubscriptionTier).where(
            SubscriptionTier.name == "advanced"
        ))).one()
        ledger = RequestLedger(
            user_id=user.id, tier_id=tier.id, request_id=str(uuid.uuid4()),
            model_name="gpt-transcribe", feature="transcription",
            cost=60.25, state=State.reserved,
        )
        session.add(ledger)
        await session.commit()
    redis = FakeRedis()
    app = _build_app(engine, user, redis)
    monkeypatch.setattr(settings, "VOICE_TRANSCRIPTION_ENABLED", True)

    def storage_down():
        raise audio_api.PrivateAudioStorageConfigurationError("unavailable")

    monkeypatch.setattr(audio_api, "ensure_audio_storage_configured", storage_down)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        storage_result = await client.get("/api/v1/audio/transcriptions/limits")
        monkeypatch.setattr(audio_api, "ensure_audio_storage_configured", lambda: None)

        async def redis_down(_key):
            raise ConnectionError("unavailable")

        original_get = redis.get
        monkeypatch.setattr(redis, "get", redis_down)
        redis_result = await client.get("/api/v1/audio/transcriptions/limits")

        async def redis_hangs(_key):
            await asyncio.Event().wait()

        monkeypatch.setattr(redis, "get", redis_hangs)
        timeout_result = await client.get("/api/v1/audio/transcriptions/limits")
        monkeypatch.setattr(redis, "get", original_get)
        redis.values[f"voice:transcription:worker:{settings.DEPLOYMENT_CHANNEL}"] = "1"
        healthy_result = await client.get("/api/v1/audio/transcriptions/limits")
        async with AsyncSession(engine, expire_on_commit=False) as session:
            saved = (await session.exec(select(RequestLedger).where(
                RequestLedger.id == ledger.id
            ))).one()
            saved.cost = 180
            session.add(saved)
            await session.commit()
        exhausted_result = await client.get("/api/v1/audio/transcriptions/limits")

    for result in (storage_result, redis_result, timeout_result, healthy_result, exhausted_result):
        assert result.status_code == 200
        body = result.json()
        assert body["entitled"] is True
        assert body["monthly_limit_minutes"] == 180
        assert body["resets_at"].endswith("Z")
        assert datetime.fromisoformat(body["resets_at"].replace("Z", "+00:00")) == (
            audio_api._next_utc_month_start(datetime.now(UTC))
        )
    assert storage_result.json()["remaining_minutes"] == pytest.approx(119.75)
    assert storage_result.json()["upload_available"] is False
    assert redis_result.json()["remaining_minutes"] == pytest.approx(119.75)
    assert redis_result.json()["upload_available"] is False
    assert timeout_result.json()["remaining_minutes"] == pytest.approx(119.75)
    assert timeout_result.json()["upload_available"] is False
    assert healthy_result.json()["upload_available"] is True
    assert exhausted_result.json()["remaining_minutes"] == 0
    assert exhausted_result.json()["upload_available"] is True
    await engine.dispose()


@pytest.mark.asyncio
async def test_monthly_transcription_allowance_is_enforced(monkeypatch, audio_samples):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)
    user = await _create_user(engine, tier_name="advanced")
    redis = FakeRedis()
    app = _build_app(engine, user, redis)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        tier = (
            await session.exec(
                select(SubscriptionTier).where(SubscriptionTier.name == "advanced")
            )
        ).one()
        session.add(
            RequestLedger(
                user_id=user.id,
                tier_id=tier.id,
                request_id=str(uuid.uuid4()),
                model_name="gpt-transcribe",
                feature="transcription",
                cost=180,
                state=State.consumed,
            )
        )
        await session.commit()

    async def _unexpected_transcribe(**_kwargs):
        raise AssertionError("Provider must not be called after allowance exhaustion")

    monkeypatch.setattr(settings, "VOICE_TRANSCRIPTION_ENABLED", True)
    monkeypatch.setattr(audio_api, "transcribe_audio", _unexpected_transcribe)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/audio/transcriptions",
            data={"client_request_id": str(uuid.uuid4()), "duration_ms": "1000"},
            files={"audio": ("voice.webm", audio_samples["webm"], "audio/webm")},
        )

    assert response.status_code == 429
    assert response.json()["detail"] == "voice_transcription_allowance_exhausted"
    await engine.dispose()


@pytest.mark.asyncio
async def test_upload_job_recovers_result_and_never_replays_same_id(monkeypatch, audio_samples):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)
    user = await _create_user(engine, tier_name="advanced")
    redis = FakeRedis()
    monkeypatch.setattr(settings, "DEPLOYMENT_CHANNEL", "beta")
    monkeypatch.setattr(settings, "VOICE_TRANSCRIPTION_ENABLED", True)
    redis.values["voice:transcription:worker:beta"] = "1"
    app = _build_app(engine, user, redis)
    objects = {}
    uploads = []
    provider_calls = 0

    async def fake_upload(key, contents):
        objects[key] = contents
        uploads.append(key)

    async def fake_download(key, *, max_bytes):
        assert len(objects[key]) <= max_bytes
        return objects[key]

    async def fake_delete(key):
        objects.pop(key, None)

    async def fake_transcribe(**kwargs):
        nonlocal provider_calls
        provider_calls += 1
        assert kwargs["timeout_seconds"] == settings.VOICE_TRANSCRIPTION_UPLOAD_TIMEOUT_SECONDS
        assert kwargs["filename"] == "audio.mp3"
        return TranscriptionResult("Recoverable transcript", 0.1, 10, 4)

    monkeypatch.setattr(audio_api, "ensure_audio_storage_configured", lambda: None)
    monkeypatch.setattr(audio_api, "upload_audio", fake_upload)
    monkeypatch.setattr(audio_api, "delete_audio", fake_delete)
    async def never_record(**_kwargs):
        raise AssertionError("A recording must not reuse an upload's request ID")
    monkeypatch.setattr(audio_api, "transcribe_audio", never_record)
    monkeypatch.setattr(audio_jobs, "download_audio", fake_download)
    monkeypatch.setattr(audio_jobs, "delete_audio", fake_delete)
    monkeypatch.setattr(audio_jobs, "transcribe_audio", fake_transcribe)
    monkeypatch.setattr(audio_jobs, "engine", engine)
    request_id = str(uuid.uuid4())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            "/api/v1/audio/transcriptions",
            data={"client_request_id": request_id, "source": "upload", "duration_ms": "1"},
            files={"audio": ("song.mp3", audio_samples["mp3"], "audio/x-mpeg")},
        )
        repeated = await client.post(
            "/api/v1/audio/transcriptions",
            data={"client_request_id": request_id, "source": "upload"},
            files={"audio": ("different.wav", audio_samples["wav"], "audio/x-wav")},
        )
        queued = await client.get(f"/api/v1/audio/transcriptions/{request_id}")
        wrong_source = await client.post(
            "/api/v1/audio/transcriptions",
            data={"client_request_id": request_id, "source": "recording"},
            files={"audio": ("voice.webm", audio_samples["webm"], "audio/webm")},
        )

    assert first.status_code == 202
    assert first.json()["status"] == "queued"
    assert first.headers["retry-after"] == "2"
    assert repeated.status_code == 202
    assert queued.json()["status"] == "queued"
    assert wrong_source.status_code == 409
    assert wrong_source.json()["detail"] == "request_id_conflict"
    assert len(uploads) == 1
    assert list(objects.values()) == [audio_samples["mp3"]]

    async with AsyncSession(engine, expire_on_commit=False) as session:
        job = await audio_jobs.claim_next_job(session)
    assert job is not None
    assert job.status == "processing"
    await audio_jobs.process_job(job)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        completed = await client.get(f"/api/v1/audio/transcriptions/{request_id}")
        retried = await client.post(
            "/api/v1/audio/transcriptions",
            data={"client_request_id": request_id, "source": "upload"},
            files={"audio": ("different.wav", audio_samples["wav"], "audio/x-wav")},
        )
    assert completed.status_code == 200
    assert completed.json()["text"] == "Recoverable transcript"
    assert retried.status_code == 200
    assert retried.json()["text"] == "Recoverable transcript"
    assert provider_calls == 1
    assert uploads == uploads[:1]
    assert objects == {}

    async with AsyncSession(engine, expire_on_commit=False) as session:
        ledger = (await session.exec(select(RequestLedger).where(
            RequestLedger.request_id == request_id,
        ))).one()
        usages = (await session.exec(select(TokenUsage).where(
            TokenUsage.request_id == request_id,
        ))).all()
    assert ledger.state == State.consumed
    assert ledger.cost == pytest.approx(audio_api.probe_audio(audio_samples["mp3"]).duration_seconds / 60)
    assert len(usages) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_account_deletion_during_object_put_cannot_queue_or_orphan_audio(monkeypatch, audio_samples):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)
    user = await _create_user(engine, tier_name="advanced")
    redis = FakeRedis()
    monkeypatch.setattr(settings, "DEPLOYMENT_CHANNEL", "beta")
    monkeypatch.setattr(settings, "VOICE_TRANSCRIPTION_ENABLED", True)
    redis.values["voice:transcription:worker:beta"] = "1"
    monkeypatch.setattr(audio_api, "ensure_audio_storage_configured", lambda: None)
    app = _build_app(engine, user, redis)
    put_started = asyncio.Event()
    finish_put = asyncio.Event()
    objects = {}

    async def paused_put(key, contents):
        put_started.set()
        await finish_put.wait()
        objects[key] = contents

    async def fake_delete(key):
        objects.pop(key, None)

    monkeypatch.setattr(audio_api, "upload_audio", paused_put)
    monkeypatch.setattr(audio_api, "delete_audio", fake_delete)
    request_id = uuid.uuid4()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pending = asyncio.create_task(client.post(
            "/api/v1/audio/transcriptions",
            data={"client_request_id": str(request_id), "source": "upload"},
            files={"audio": ("song.mp3", audio_samples["mp3"], "audio/mpeg")},
        ))
        await asyncio.wait_for(put_started.wait(), 5)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            locked_user = (await session.exec(select(AppUser).where(AppUser.id == user.id).with_for_update())).one()
            job = (await session.exec(select(AudioTranscriptionJob).where(
                AudioTranscriptionJob.user_id == user.id,
                AudioTranscriptionJob.request_id == request_id,
            ).with_for_update())).one()
            assert job.status == "storing"
            job.status = "cancel_pending"
            job.error_code = "voice_transcription_account_deleted"
            ledger = (await session.exec(select(RequestLedger).where(
                RequestLedger.user_id == user.id,
                RequestLedger.request_id == str(request_id),
            ).with_for_update())).one()
            ledger.state = State.failed
            locked_user.deleted_at = job.updated_at
            session.add_all((locked_user, job, ledger))
            await session.commit()
        finish_put.set()
        result = await asyncio.wait_for(pending, 5)
    assert result.status_code == 409
    assert result.json()["detail"] == "voice_transcription_account_deleted"
    assert objects == {}
    async with AsyncSession(engine, expire_on_commit=False) as session:
        job = (await session.exec(select(AudioTranscriptionJob).where(
            AudioTranscriptionJob.request_id == request_id,
        ))).one()
        assert job.status == "cancel_pending"
    await engine.dispose()


def test_probe_accepts_real_thirty_minute_compressed_files(tmp_path):
    for extension, codec in (("webm", "libopus"), ("mp3", "libmp3lame")):
        target = tmp_path / f"duration.{extension}"
        subprocess.run([
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
            "anullsrc=r=16000:cl=mono", "-t", "1800", "-c:a", codec,
            "-b:a", "24k", str(target),
        ], capture_output=True, check=True)
        contents = target.read_bytes()
        assert len(contents) < 20 * 1024 * 1024
        probed = probe_audio(contents)
        assert 1799 <= probed.duration_seconds <= 1801
