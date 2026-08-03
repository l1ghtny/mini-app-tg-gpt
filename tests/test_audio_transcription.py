import os
import uuid
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
from app.db.models import AppUser, RequestLedger, State, TokenUsage
from app.db.subscription_tiers import SubscriptionTier, UserSubscription
from app.services.transcription_service import TranscriptionResult
from app.services import transcription_service


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

    monkeypatch.setattr(
        transcription_service,
        "_client",
        SimpleNamespace(audio=SimpleNamespace(transcriptions=FakeTranscriptions())),
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
async def test_transcription_requires_a_tier_allowance(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)
    user = await _create_user(engine, tier_name=None)
    redis = FakeRedis()
    app = _build_app(engine, user, redis)
    monkeypatch.setattr(settings, "VOICE_TRANSCRIPTION_ENABLED", True)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/audio/transcriptions",
            data={"client_request_id": str(uuid.uuid4()), "duration_ms": "1000"},
            files={"audio": ("voice.webm", b"audio", "audio/webm")},
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "voice_transcription_subscription_required"
    await engine.dispose()


@pytest.mark.asyncio
async def test_transcription_is_idempotent_and_records_usage(monkeypatch):
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
        assert kwargs["audio"] == b"audio"
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
            files={"audio": ("voice.webm", b"audio", "audio/webm")},
        )
        second = await client.post(
            "/api/v1/audio/transcriptions",
            data={"client_request_id": request_id, "duration_ms": "2400"},
            files={"audio": ("voice.webm", b"audio", "audio/webm")},
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
    assert ledger.cost == pytest.approx(2.5 / 60)
    assert usage.input_tokens == 12
    assert usage.output_tokens == 4
    assert usage.total_cost > 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_zero_price_smooth_tier_receives_beta_allowance(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)
    user = await _create_user(engine, tier_name="Smooth tier")
    redis = FakeRedis()
    app = _build_app(engine, user, redis)

    async def _fake_transcribe_audio(**_kwargs):
        return TranscriptionResult(
            text="Smooth transcript",
            duration_seconds=1.5,
            input_tokens=6,
            output_tokens=2,
        )

    monkeypatch.setattr(settings, "VOICE_TRANSCRIPTION_ENABLED", True)
    monkeypatch.setattr(audio_api, "transcribe_audio", _fake_transcribe_audio)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/audio/transcriptions",
            data={"client_request_id": str(uuid.uuid4()), "duration_ms": "1500"},
            files={"audio": ("voice.webm", b"audio", "audio/webm")},
        )

    assert response.status_code == 200
    assert response.json()["text"] == "Smooth transcript"
    await engine.dispose()


@pytest.mark.asyncio
async def test_monthly_transcription_allowance_is_enforced(monkeypatch):
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
            files={"audio": ("voice.webm", b"audio", "audio/webm")},
        )

    assert response.status_code == 429
    assert response.json()["detail"] == "voice_transcription_allowance_exhausted"
    await engine.dispose()
