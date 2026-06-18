from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import uuid

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.api import admin_broadcast


class _FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.lists: dict[str, list[str]] = {}

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def expire(self, key: str, seconds: int) -> None:
        return None

    async def hset(self, key: str, field: str | None = None, value: str | None = None, mapping: dict[str, str] | None = None) -> None:
        bucket = self.hashes.setdefault(key, {})
        if mapping is not None:
            bucket.update(mapping)
            return
        assert field is not None
        assert value is not None
        bucket[field] = value

    async def hget(self, key: str, field: str) -> str | None:
        return self.hashes.get(key, {}).get(field)

    async def hmget(self, key: str, fields: list[str]) -> list[str | None]:
        bucket = self.hashes.get(key, {})
        return [bucket.get(field) for field in fields]

    async def lpush(self, key: str, value: str) -> None:
        bucket = self.lists.setdefault(key, [])
        bucket.insert(0, value)

    async def ltrim(self, key: str, start: int, end: int) -> None:
        bucket = self.lists.get(key, [])
        if end < 0:
            self.lists[key] = bucket[start:]
            return
        self.lists[key] = bucket[start: end + 1]

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        bucket = self.lists.get(key, [])
        if end < 0:
            return bucket[start:]
        return bucket[start: end + 1]

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


def _fake_admin_user(telegram_id: int = 123456) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), telegram_id=telegram_id)


@pytest.mark.asyncio
async def test_broadcast_worker_tracks_delivery_outcomes(monkeypatch):
    fake_redis = _FakeRedis()
    _FakeBot.calls = {}
    recipients = [_recipient(1), _recipient(2), _recipient(3), _recipient(4)]

    async def _fake_store():
        return fake_redis

    monkeypatch.setattr(admin_broadcast, "_new_broadcast_store", _fake_store)
    monkeypatch.setattr(admin_broadcast, "Bot", _FakeBot)
    monkeypatch.setattr(admin_broadcast, "TelegramRetryAfter", _FakeRetryAfter)
    monkeypatch.setattr(admin_broadcast, "TelegramForbiddenError", _FakeForbiddenError)
    monkeypatch.setattr(admin_broadcast, "TelegramBadRequest", _FakeBadRequestError)

    job = admin_broadcast.BroadcastJobStatus(
        job_id="job-1",
        status="queued",
        total=4,
        filters=admin_broadcast.BroadcastFilters(),
        per_second=30.0,
        requested_limit=None,
        text_preview="hello",
        recipient_fingerprint=admin_broadcast._build_recipient_fingerprint(recipients),
        attempted=0,
        sent=0,
        failed=0,
        pending=4,
        started_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    await admin_broadcast._write_job(fake_redis, job)
    await admin_broadcast._write_recipient_snapshot(fake_redis, "job-1", recipients)

    await admin_broadcast._broadcast_worker(
        job_id="job-1",
        text="hello",
        recipients=recipients,
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

    recipient_page = await admin_broadcast._list_recipient_statuses(fake_redis, "job-1", offset=0, limit=10, status=None)
    assert recipient_page.total == 4
    assert [item.status for item in recipient_page.items] == ["sent", "failed", "sent", "failed"]
    assert recipient_page.items[2].attempts == 2
    assert recipient_page.items[2].delivered_at is not None

    failed_page = await admin_broadcast._list_recipient_statuses(fake_redis, "job-1", offset=0, limit=10, status="failed")
    assert failed_page.total == 2
    assert {item.telegram_id for item in failed_page.items} == {2, 4}
    assert {item.error_type for item in failed_page.items} == {"_FakeForbiddenError", "_FakeBadRequestError"}


@pytest.mark.asyncio
async def test_read_job_marks_stale_running_job():
    fake_redis = _FakeRedis()
    job = admin_broadcast.BroadcastJobStatus(
        job_id="job-stale",
        status="running",
        total=10,
        filters=admin_broadcast.BroadcastFilters(),
        per_second=15.0,
        requested_limit=None,
        text_preview="hello",
        recipient_fingerprint="abc",
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


@pytest.mark.asyncio
async def test_list_jobs_returns_most_recent_first():
    fake_redis = _FakeRedis()
    first = admin_broadcast.BroadcastJobStatus(
        job_id="job-a",
        status="completed",
        total=1,
        filters=admin_broadcast.BroadcastFilters(),
        per_second=15.0,
        requested_limit=None,
        text_preview="first",
        recipient_fingerprint="fp-a",
        attempted=1,
        sent=1,
        failed=0,
        pending=0,
        started_at=datetime.now(UTC) - timedelta(minutes=2),
        updated_at=datetime.now(UTC) - timedelta(minutes=2),
        finished_at=datetime.now(UTC) - timedelta(minutes=2),
    )
    second = admin_broadcast.BroadcastJobStatus(
        job_id="job-b",
        status="queued",
        total=2,
        filters=admin_broadcast.BroadcastFilters(active_within_days=1),
        per_second=10.0,
        requested_limit=100,
        text_preview="second",
        recipient_fingerprint="fp-b",
        attempted=0,
        sent=0,
        failed=0,
        pending=2,
        started_at=datetime.now(UTC) - timedelta(minutes=1),
        updated_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    await admin_broadcast._write_job(fake_redis, first)
    await admin_broadcast._register_job(fake_redis, "job-a")
    await admin_broadcast._write_job(fake_redis, second)
    await admin_broadcast._register_job(fake_redis, "job-b")

    jobs = await admin_broadcast._list_jobs(fake_redis, 10)
    assert [job.job_id for job in jobs] == ["job-b", "job-a"]
    assert jobs[0].filters.active_within_days == 1
    assert jobs[1].status == "completed"


@pytest.mark.asyncio
async def test_apply_sentry_cohort_filters_registered_but_not_opened(monkeypatch):
    recipients = [_recipient(1), _recipient(2), _recipient(3)]
    cohort_ids = {recipients[0].user_id, recipients[2].user_id}

    async def _fake_registered_but_not_opened(_hours: int, *, limit: int = 5000):
        return cohort_ids

    monkeypatch.setattr(
        admin_broadcast.sentry_audiences,
        "registered_but_not_opened_within_hours",
        _fake_registered_but_not_opened,
    )

    filtered = await admin_broadcast._apply_sentry_cohort_filters(
        recipients,
        admin_broadcast.BroadcastFilters(registered_but_not_opened_within_hours=24),
    )

    assert [item.user_id for item in filtered] == [recipients[0].user_id, recipients[2].user_id]


@pytest.mark.asyncio
async def test_apply_broadcast_cooldown_filters_recent_recipient():
    fake_redis = _FakeRedis()
    recent = _recipient(1)
    older = _recipient(2)
    await fake_redis.set(
        admin_broadcast._recipient_last_sent_key(recent.user_id),
        datetime.now(UTC).isoformat(),
    )
    await fake_redis.set(
        admin_broadcast._recipient_last_sent_key(older.user_id),
        (datetime.now(UTC) - timedelta(hours=72)).isoformat(),
    )

    filtered, excluded = await admin_broadcast._apply_broadcast_cooldown(
        fake_redis,
        [recent, older],
        min_hours_since_last_broadcast=48,
    )

    assert excluded == 1
    assert [item.user_id for item in filtered] == [older.user_id]


@pytest.mark.asyncio
async def test_broadcast_worker_honors_preexisting_cancel_request(monkeypatch):
    fake_redis = _FakeRedis()
    _FakeBot.calls = {}
    recipients = [_recipient(1), _recipient(2)]

    async def _fake_store():
        return fake_redis

    monkeypatch.setattr(admin_broadcast, "_new_broadcast_store", _fake_store)
    monkeypatch.setattr(admin_broadcast, "Bot", _FakeBot)

    now = datetime.now(UTC)
    job = admin_broadcast.BroadcastJobStatus(
        job_id="job-cancelled",
        status="cancelling",
        total=2,
        filters=admin_broadcast.BroadcastFilters(),
        per_second=15.0,
        requested_limit=None,
        text_preview="hello",
        recipient_fingerprint=admin_broadcast._build_recipient_fingerprint(recipients),
        attempted=0,
        sent=0,
        failed=0,
        pending=2,
        started_at=now,
        updated_at=now,
        cancel_requested_at=now,
    )
    await admin_broadcast._write_job(fake_redis, job)
    await admin_broadcast._write_recipient_snapshot(fake_redis, "job-cancelled", recipients)

    await admin_broadcast._broadcast_worker(
        job_id="job-cancelled",
        text="hello",
        recipients=recipients,
        per_second=30.0,
    )

    saved = await admin_broadcast._read_job(fake_redis, "job-cancelled")
    assert saved is not None
    assert saved.status == "cancelled"
    assert saved.cancelled_at is not None
    assert saved.attempted == 0
    assert _FakeBot.calls == {}


@pytest.mark.asyncio
async def test_enqueue_broadcast_job_reuses_existing_job_for_idempotency_key(monkeypatch):
    fake_redis = _FakeRedis()
    recipients = [_recipient(1), _recipient(2)]

    async def _fake_select(_session, _filters, _limit, _redis):
        return recipients, 0

    monkeypatch.setattr(admin_broadcast, "_select_recipients", _fake_select)
    monkeypatch.setattr(admin_broadcast.settings, "BROADCAST_ADMIN_TOKEN", "secret")

    req = admin_broadcast.BroadcastRequest(
        text="hello",
        idempotency_key="admin-broadcast-001",
    )
    background_tasks = BackgroundTasks()
    admin_user = _fake_admin_user()

    first = await admin_broadcast._enqueue_broadcast_job(
        req,
        background_tasks,
        _FakeSession(),
        fake_redis,
        "secret",
        admin_user,
    )
    second = await admin_broadcast._enqueue_broadcast_job(
        req,
        background_tasks,
        _FakeSession(),
        fake_redis,
        "secret",
        admin_user,
    )

    assert first.job_id == second.job_id
    assert first.idempotency_key == "admin-broadcast-001"
    jobs = await admin_broadcast._list_jobs(fake_redis, 10)
    assert len(jobs) == 1
    assert jobs[0].created_by_telegram_id == admin_user.telegram_id


@pytest.mark.asyncio
async def test_enqueue_broadcast_job_rejects_recipient_fingerprint_mismatch(monkeypatch):
    fake_redis = _FakeRedis()
    recipients = [_recipient(1), _recipient(2)]

    async def _fake_select(_session, _filters, _limit, _redis):
        return recipients, 0

    monkeypatch.setattr(admin_broadcast, "_select_recipients", _fake_select)
    monkeypatch.setattr(admin_broadcast.settings, "BROADCAST_ADMIN_TOKEN", "secret")

    req = admin_broadcast.BroadcastRequest(
        text="hello",
        expected_recipient_fingerprint="x" * 64,
    )

    with pytest.raises(HTTPException) as exc_info:
        await admin_broadcast._enqueue_broadcast_job(
            req,
            BackgroundTasks(),
            _FakeSession(),
            fake_redis,
            "secret",
            _fake_admin_user(),
        )

    assert exc_info.value.status_code == 409


def test_check_admin_access_requires_authenticated_allowlisted_user(monkeypatch):
    monkeypatch.setattr(admin_broadcast.settings, "BROADCAST_ADMIN_TOKEN", "secret")
    monkeypatch.setattr(admin_broadcast.settings, "BROADCAST_ADMIN_TELEGRAM_ALLOWLIST", "111,222")

    with pytest.raises(HTTPException) as missing_user_exc:
        admin_broadcast._check_admin_access("secret", None)
    assert missing_user_exc.value.status_code == 401

    with pytest.raises(HTTPException) as forbidden_exc:
        admin_broadcast._check_admin_access("secret", _fake_admin_user(telegram_id=333))
    assert forbidden_exc.value.status_code == 403

    admin_broadcast._check_admin_access("secret", _fake_admin_user(telegram_id=111))
