"""Release regressions for durable uploaded-audio jobs.

These tests use only TEST_DATABASE_URL. The suite's autouse fixture rebuilds that
disposable database, so they must not run beside another DB test process.
"""

import os
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api import audio as audio_api
from app.api.dependencies import get_current_user, get_redis
from app.core.config import settings
from app.db.database import get_session
from app.db.models import AudioTranscriptionJob, AppUser, RequestLedger, State, TokenUsage, utcnow_naive
from app.db.subscription_tiers import SubscriptionTier, UserSubscription
from app.services import audio_jobs


class FakeRedis:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def incr(self, key):
        self.values[key] = int(self.values.get(key, 0)) + 1
        return self.values[key]

    async def expire(self, key, _seconds):
        return key in self.values


@pytest.fixture
async def db_engine():
    url = os.getenv("TEST_DATABASE_URL")
    assert url and "test" in url.rsplit("/", 1)[-1].lower()
    engine = create_async_engine(url, future=True, echo=False)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _user(engine, *, telegram_id: int, subscribed: bool = True) -> AppUser:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=telegram_id)
        session.add(user)
        await session.flush()
        if subscribed:
            tier = (await session.exec(select(SubscriptionTier).where(SubscriptionTier.name == "advanced"))).one()
            session.add(UserSubscription(user_id=user.id, tier_id=tier.id))
        await session.commit()
        return user


def _app(engine, user: AppUser, redis: FakeRedis) -> FastAPI:
    app = FastAPI()
    app.include_router(audio_api.audio, prefix="/api/v1")

    async def session_dependency():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_session] = session_dependency
    app.dependency_overrides[get_redis] = lambda: redis
    return app


def _job(user: AppUser, *, channel: str, status: str, duration: float = 5.0) -> AudioTranscriptionJob:
    now = utcnow_naive()
    return AudioTranscriptionJob(
        user_id=user.id, request_id=uuid.uuid4(), channel=channel, status=status,
        model_name=settings.VOICE_TRANSCRIPTION_MODEL, audio_key=f"transcriptions/{channel}/{user.id}/{uuid.uuid4()}.mp3",
        audio_extension=".mp3", duration_seconds=duration,
        created_at=now, updated_at=now, expires_at=now + timedelta(hours=24),
    )


@pytest.mark.asyncio
async def test_status_and_claim_are_isolated_by_owner_and_channel(db_engine, monkeypatch):
    owner = await _user(db_engine, telegram_id=731001)
    other = await _user(db_engine, telegram_id=731002, subscribed=False)
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        job = _job(owner, channel="production", status="queued")
        session.add(job)
        await session.commit()

    monkeypatch.setattr(settings, "VOICE_TRANSCRIPTION_ENABLED", True)
    monkeypatch.setattr(settings, "DEPLOYMENT_CHANNEL", "beta")
    redis = FakeRedis()
    redis.values["voice:transcription:worker:beta"] = "1"
    async with AsyncClient(transport=ASGITransport(app=_app(db_engine, owner, redis)), base_url="http://test") as client:
        beta_read = await client.get(f"/api/v1/audio/transcriptions/{job.request_id}")
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        claimed = await audio_jobs.claim_next_job(session)
    assert beta_read.status_code == 404
    assert claimed is None

    monkeypatch.setattr(settings, "DEPLOYMENT_CHANNEL", "production")
    async with AsyncClient(transport=ASGITransport(app=_app(db_engine, other, redis)), base_url="http://test") as client:
        other_read = await client.get(f"/api/v1/audio/transcriptions/{job.request_id}")
    async with AsyncClient(transport=ASGITransport(app=_app(db_engine, owner, redis)), base_url="http://test") as client:
        owner_read = await client.get(f"/api/v1/audio/transcriptions/{job.request_id}")
    assert other_read.status_code == 404
    assert owner_read.status_code == 200
    assert owner_read.json()["status"] == "queued"


@pytest.mark.asyncio
async def test_expired_processing_lease_becomes_uncertain_without_provider_replay(db_engine, monkeypatch):
    user = await _user(db_engine, telegram_id=731003)
    now = utcnow_naive()
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        tier = (await session.exec(select(SubscriptionTier).where(SubscriptionTier.name == "advanced"))).one()
        job = _job(user, channel="beta", status="processing")
        job.attempt_started_at = now - timedelta(minutes=30)
        job.lease_expires_at = now - timedelta(seconds=1)
        session.add(job)
        session.add(RequestLedger(
            user_id=user.id, tier_id=tier.id, request_id=str(job.request_id),
            model_name=job.model_name, feature="transcription", cost=job.duration_seconds / 60,
            state=State.reserved,
        ))
        await session.commit()

    async def must_not_transcribe(**_kwargs):
        raise AssertionError("A stale paid operation must never be replayed")

    async def delete_audio(_key):
        return None

    monkeypatch.setattr(settings, "DEPLOYMENT_CHANNEL", "beta")
    monkeypatch.setattr(audio_jobs, "engine", db_engine)
    monkeypatch.setattr(audio_jobs, "transcribe_audio", must_not_transcribe)
    monkeypatch.setattr(audio_jobs, "delete_audio", delete_audio)
    await audio_jobs.maintain_jobs()

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        saved = (await session.exec(select(AudioTranscriptionJob).where(AudioTranscriptionJob.id == job.id))).one()
        ledger = (await session.exec(select(RequestLedger).where(RequestLedger.request_id == str(job.request_id)))).one()
        usage = (await session.exec(select(TokenUsage).where(TokenUsage.request_id == str(job.request_id)))).all()
    assert saved.status == "uncertain"
    assert saved.error_code == "voice_transcription_result_uncertain"
    assert saved.audio_key is None
    assert ledger.state == State.failed
    assert usage == []


@pytest.mark.asyncio
async def test_expired_transcript_is_scrubbed_even_when_private_delete_fails(db_engine, monkeypatch):
    user = await _user(db_engine, telegram_id=731004)
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        job = _job(user, channel="beta", status="completed")
        job.transcript_text = "private words that must expire"
        job.expires_at = utcnow_naive() - timedelta(seconds=1)
        session.add(job)
        await session.commit()

    async def broken_delete(_key):
        raise RuntimeError("private storage temporarily unavailable")

    monkeypatch.setattr(settings, "DEPLOYMENT_CHANNEL", "beta")
    monkeypatch.setattr(settings, "VOICE_TRANSCRIPTION_ENABLED", True)
    monkeypatch.setattr(audio_jobs, "engine", db_engine)
    monkeypatch.setattr(audio_jobs, "delete_audio", broken_delete)
    await audio_jobs.maintain_jobs()

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        saved = (await session.exec(select(AudioTranscriptionJob).where(AudioTranscriptionJob.id == job.id))).one()
    assert saved.transcript_text is None
    assert saved.audio_key is not None  # retained only so object deletion can be retried
    async with AsyncClient(transport=ASGITransport(app=_app(db_engine, user, FakeRedis())), base_url="http://test") as client:
        expired = await client.get(f"/api/v1/audio/transcriptions/{job.request_id}")
    assert expired.status_code == 404


@pytest.mark.asyncio
async def test_beta_upload_counts_exact_production_reservation_against_shared_quota(db_engine, monkeypatch):
    user = await _user(db_engine, telegram_id=731005)
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        tier = (await session.exec(select(SubscriptionTier).where(SubscriptionTier.name == "advanced"))).one()
        tier.monthly_transcription_minutes = 1
        production_job = _job(user, channel="production", status="processing", duration=59.0)
        production_job.lease_expires_at = utcnow_naive() + timedelta(minutes=10)
        session.add(tier)
        session.add(production_job)
        session.add(RequestLedger(
            user_id=user.id, tier_id=tier.id, request_id=str(production_job.request_id),
            model_name=production_job.model_name, feature="transcription", cost=59.0 / 60,
            state=State.reserved,
        ))
        await session.commit()

    monkeypatch.setattr(settings, "DEPLOYMENT_CHANNEL", "beta")
    monkeypatch.setattr(settings, "VOICE_TRANSCRIPTION_ENABLED", True)
    monkeypatch.setattr(audio_api, "ensure_audio_storage_configured", lambda: None)
    monkeypatch.setattr(audio_api, "probe_audio", lambda _contents: SimpleNamespace(extension=".mp3", duration_seconds=2.5))
    async def must_not_upload(_key, _contents):
        raise AssertionError("Quota rejection must happen before private upload")
    monkeypatch.setattr(audio_api, "upload_audio", must_not_upload)
    redis = FakeRedis()
    redis.values["voice:transcription:worker:beta"] = "1"
    async with AsyncClient(transport=ASGITransport(app=_app(db_engine, user, redis)), base_url="http://test") as client:
        limits = await client.get("/api/v1/audio/transcriptions/limits")
        rejected = await client.post(
            "/api/v1/audio/transcriptions",
            data={"client_request_id": str(uuid.uuid4()), "source": "upload", "duration_ms": "1"},
            files={"audio": ("sample.mp3", b"audio contents", "audio/mpeg")},
        )
    assert limits.status_code == 200
    assert limits.json()["remaining_minutes"] == pytest.approx(1 - 59.0 / 60)
    assert rejected.status_code == 429
    assert rejected.json()["detail"] == "voice_transcription_allowance_exhausted"
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        jobs = (await session.exec(select(AudioTranscriptionJob))).all()
        ledgers = (await session.exec(select(RequestLedger))).all()
    assert len(jobs) == 1
    assert len(ledgers) == 1
