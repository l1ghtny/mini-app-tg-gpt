"""A standalone worker must register every SQLModel foreign-key target."""

import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest


def test_standalone_worker_registers_foreign_key_tables():
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    env.setdefault("OPENAI_API_KEY", "test")
    env.setdefault("R2_BUCKET", "public-test")
    env.setdefault("R2_ENDPOINT", "https://example.invalid")
    env.setdefault("R2_ACCESS_KEY_ID", "test")
    env.setdefault("R2_SECRET_ACCESS_KEY", "test")
    result = subprocess.run(
        [sys.executable, "-c", (
            "import jobs.audio_transcription_worker; "
            "from sqlmodel import SQLModel; "
            "tables = SQLModel.metadata.sorted_tables; "
            "assert any(table.name == 'user_usage_pack' for table in tables); "
            "assert any(table.name == 'request_ledger' for table in tables)"
        )],
        cwd=root, env=env, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires disposable test database")
def test_standalone_worker_claims_and_settles_job_without_provider():
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["DATABASE_URL"] = env["TEST_DATABASE_URL"]
    env["DEPLOYMENT_CHANNEL"] = "beta"
    env.setdefault("OPENAI_API_KEY", "test")
    env.setdefault("R2_BUCKET", "public-test")
    env.setdefault("R2_ENDPOINT", "https://example.invalid")
    env.setdefault("R2_ACCESS_KEY_ID", "test")
    env.setdefault("R2_SECRET_ACCESS_KEY", "test")
    script = dedent("""
        import asyncio
        import uuid
        import jobs.audio_transcription_worker
        from sqlmodel import select
        from sqlmodel.ext.asyncio.session import AsyncSession
        from app.db.database import engine
        from app.db.models import AppUser, AudioTranscriptionJob, RequestLedger, State, utcnow_naive
        from app.db.subscription_tiers import SubscriptionTier
        from app.services import audio_jobs
        from app.services.transcription_service import TranscriptionResult
        from datetime import timedelta

        async def fake_download(_key, *, max_bytes):
            return b'fake audio'

        async def fake_transcribe(**_kwargs):
            return TranscriptionResult('Settled locally', 1.0, 3, 2)

        async def fake_delete(_key):
            return None

        async def main():
            audio_jobs.download_audio = fake_download
            audio_jobs.transcribe_audio = fake_transcribe
            audio_jobs.delete_audio = fake_delete
            async with AsyncSession(engine, expire_on_commit=False) as session:
                tier = (await session.exec(select(SubscriptionTier).where(SubscriptionTier.name == 'advanced'))).one()
                user = AppUser(telegram_id=730900000)
                session.add(user)
                await session.flush()
                request_id = uuid.uuid4()
                now = utcnow_naive()
                session.add(AudioTranscriptionJob(
                    user_id=user.id, request_id=request_id, channel='beta', status='queued',
                    model_name='gpt-transcribe', audio_key='transcriptions/beta/test.mp3',
                    audio_extension='.mp3', duration_seconds=3.0,
                    created_at=now, updated_at=now, expires_at=now + timedelta(hours=24),
                ))
                session.add(RequestLedger(
                    user_id=user.id, tier_id=tier.id, request_id=str(request_id),
                    model_name='gpt-transcribe', feature='transcription', cost=0.05,
                    state=State.reserved,
                ))
                await session.commit()
            async with AsyncSession(engine, expire_on_commit=False) as session:
                job = await audio_jobs.claim_next_job(session)
            assert job is not None and job.status == 'processing'
            await audio_jobs.process_job(job)
            async with AsyncSession(engine, expire_on_commit=False) as session:
                saved = (await session.exec(select(AudioTranscriptionJob).where(
                    AudioTranscriptionJob.request_id == request_id))).one()
                ledger = (await session.exec(select(RequestLedger).where(
                    RequestLedger.request_id == str(request_id)))).one()
            assert saved.status == 'completed' and saved.transcript_text == 'Settled locally'
            assert ledger.state == State.consumed
            await engine.dispose()

        asyncio.run(main())
    """)
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=root, env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
