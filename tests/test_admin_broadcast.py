from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import uuid

import pytest

from app.api import admin_broadcast


class _FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def expire(self, key: str, seconds: int) -> None:
        return None

    async def aclose(self) -> None:
        return None


class _FakeSession:
    async def close(self) -> None:
        return None


class _FakeRetryAfter(Exception):
    def __init__(self, retry_after: float):
        super().__init__(f"retry after {retry_after}")
        self.retry_after = retry_after


class _FakeForbiddenError(Exception):
    pass


class _FakeBadRequestError(Exception):
    pass


class _FakeBot:
    calls: dict[int, int] = {}

    def __init__(self, token: str):
        self.token = token
        self.session = _FakeSession()

    async def send_message(self, chat_id: int, text: str) -> None:
        _FakeBot.calls[chat_id] = _FakeBot.calls.get(chat_id, 0) + 1
        if chat_id == 2:
            raise _FakeForbiddenError("blocked by user")
        if chat_id == 3 and _FakeBot.calls[chat_id] == 1:
            raise _FakeRetryAfter(0)
        if chat_id == 4:
            raise _FakeBadRequestError("chat not found")


def _recipient(telegram_id: int) -> admin_broadcast._Recipient:
    return admin_broadcast._Recipient(
        user_id=uuid.uuid4(),
        telegram_id=telegram_id,
        tier_id=uuid.uuid4(),
        tier_name="Welcoming Bonus",
        onboarded_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_broadcast_worker_tracks_delivery_outcomes(monkeypatch):
    fake_redis = _FakeRedis()
    _FakeBot.calls = {}

    async def _fake_store():
        return fake_redis

    monkeypatch.setattr(admin_broadcast, "_new_broadcast_store", _fake_store)
    monkeypatch.setattr(admin_broadcast, "Bot", _FakeBot)
    monkeypatch.setattr(admin_broadcast, "TelegramRetryAfter", _FakeRetryAfter)
    monkeypatch.setattr(admin_broadcast, "TelegramForbiddenError", _FakeForbiddenError)
    monkeypatch.setattr(admin_broadcast, "TelegramBadRequest", _FakeBadRequestError)

    job = admin_broadcast.BroadcastJobStatus(
        job_id="job-1",
        status="running",
        total=4,
        attempted=0,
        sent=0,
        failed=0,
        pending=4,
        started_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    await admin_broadcast._write_job(fake_redis, job)

    await admin_broadcast._broadcast_worker(
        job_id="job-1",
        text="hello",
        recipients=[_recipient(1), _recipient(2), _recipient(3), _recipient(4)],
        per_second=30.0,
    )

    saved = await admin_broadcast._read_job(fake_redis, "job-1")
    assert saved is not None
    assert saved.status == "completed"
    assert saved.total == 4
    assert saved.attempted == 4
    assert saved.sent == 2
    assert saved.failed == 2
    assert saved.pending == 0
    assert saved.forbidden == 1
    assert saved.bad_request == 1
    assert saved.retried == 1
    assert saved.retry_succeeded == 1
    assert saved.finished_at is not None
    assert saved.last_attempted_telegram_id == 4
    assert len(saved.failure_samples) == 2
    assert {sample.error_type for sample in saved.failure_samples} == {
        "_FakeForbiddenError",
        "_FakeBadRequestError",
    }


@pytest.mark.asyncio
async def test_read_job_marks_stale_running_job():
    fake_redis = _FakeRedis()
    job = admin_broadcast.BroadcastJobStatus(
        job_id="job-stale",
        status="running",
        total=10,
        attempted=3,
        sent=3,
        failed=0,
        pending=7,
        started_at=datetime.now(UTC) - timedelta(minutes=10),
        updated_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    await admin_broadcast._write_job(fake_redis, job)

    saved = await admin_broadcast._read_job(fake_redis, "job-stale")
    assert saved is not None
    assert saved.is_stale is True
    assert saved.pending == 7
