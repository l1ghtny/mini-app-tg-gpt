import asyncio
import hashlib
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from fastapi.security import OAuth2PasswordBearer
from fastapi.responses import HTMLResponse
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import or_
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.dependencies import get_redis
from app.core.config import settings
from app.db.database import get_session
from app.db.models import AppUser, RequestLedger
from app.db.subscription_tiers import SubscriptionStatus, SubscriptionTier, UserSubscription
from app.redis.settings import settings as redis_settings


admin_broadcast = APIRouter(tags=["admin"], prefix="/admin/broadcast")
_optional_oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/debug-login",
    scheme_name="BroadcastBearer",
    auto_error=False,
)


@dataclass
class _Recipient:
    user_id: uuid.UUID
    telegram_id: int
    tier_id: uuid.UUID
    tier_name: str
    onboarded_at: datetime


class BroadcastFilters(BaseModel):
    tier_ids: list[uuid.UUID] = []
    tier_names: list[str] = []
    onboarded_from: Optional[datetime] = None
    onboarded_to: Optional[datetime] = None
    has_sent_first_message: Optional[bool] = None
    campaigns: list[str] = []
    active_within_days: Optional[int] = Field(default=None, ge=1, le=365)


class BroadcastRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4096)
    filters: BroadcastFilters = Field(default_factory=BroadcastFilters)
    per_second: float = Field(default=15.0, ge=0.5, le=30.0)
    limit: Optional[int] = Field(default=None, ge=1, le=50000)
    expected_recipient_fingerprint: Optional[str] = Field(default=None, min_length=64, max_length=64)
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=128)


class BroadcastRecipientStatus(BaseModel):
    user_id: str
    telegram_id: int
    tier_id: str
    tier_name: str
    onboarded_at: datetime
    status: str = "pending"
    attempts: int = 0
    attempted_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None


class BroadcastPreviewResponse(BaseModel):
    recipients: int
    recipient_fingerprint: str
    sample: list[BroadcastRecipientStatus]


class BroadcastFailureSample(BaseModel):
    user_id: str
    telegram_id: int
    error_type: str
    error_message: str


class BroadcastSendResponse(BaseModel):
    job_id: str
    recipients: int
    status: str
    poll_url: str
    recipients_url: str
    attempted: int
    sent: int
    failed: int
    pending: int
    updated_at: datetime
    idempotency_key: Optional[str] = None
    note: str


class BroadcastJobStatus(BaseModel):
    job_id: str
    status: str
    total: int
    filters: BroadcastFilters = Field(default_factory=BroadcastFilters)
    per_second: float
    requested_limit: Optional[int] = None
    text_preview: str
    recipient_fingerprint: str
    attempted: int
    sent: int
    failed: int
    pending: int
    forbidden: int = 0
    bad_request: int = 0
    retried: int = 0
    retry_succeeded: int = 0
    started_at: datetime
    updated_at: datetime
    finished_at: Optional[datetime] = None
    cancel_requested_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    last_error: Optional[str] = None
    last_attempted_user_id: Optional[str] = None
    last_attempted_telegram_id: Optional[int] = None
    runner_id: Optional[str] = None
    created_by_user_id: Optional[str] = None
    created_by_telegram_id: Optional[int] = None
    expected_recipient_fingerprint: Optional[str] = None
    idempotency_key: Optional[str] = None
    note: str = "sent means Telegram accepted the message for delivery; it does not guarantee the user opened it"
    failure_samples: list[BroadcastFailureSample] = Field(default_factory=list)
    is_stale: bool = False


class BroadcastJobListResponse(BaseModel):
    items: list[BroadcastJobStatus]


class BroadcastRecipientPage(BaseModel):
    job_id: str
    total: int
    offset: int
    limit: int
    items: list[BroadcastRecipientStatus]


class BroadcastCancelResponse(BaseModel):
    job_id: str
    status: str
    cancel_requested_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    updated_at: datetime
    note: str


_BROADCAST_JOB_TTL_SECONDS = 7 * 24 * 60 * 60
_BROADCAST_JOB_STALE_AFTER_SECONDS = 120
_BROADCAST_FAILURE_SAMPLE_LIMIT = 20
_BROADCAST_JOB_LIST_KEY = "broadcast:jobs"
_BROADCAST_JOB_LIST_MAX = 100
_BROADCAST_IDEMPOTENCY_KEY_PREFIX = "broadcast:idempotency:"


def _job_key(job_id: str) -> str:
    return f"broadcast:job:{job_id}"


def _job_recipient_order_key(job_id: str) -> str:
    return f"broadcast:job:{job_id}:recipient_order"


def _job_recipient_state_key(job_id: str) -> str:
    return f"broadcast:job:{job_id}:recipient_state"


def _broadcast_idempotency_key(idempotency_key: str) -> str:
    return f"{_BROADCAST_IDEMPOTENCY_KEY_PREFIX}{idempotency_key}"


def _refresh_job_derived_fields(job: BroadcastJobStatus) -> BroadcastJobStatus:
    job.pending = max(job.total - job.attempted, 0)
    return job


def _mark_job_staleness(job: BroadcastJobStatus) -> BroadcastJobStatus:
    if job.status not in {"running", "queued"}:
        job.is_stale = False
        return job
    age = (datetime.now(UTC) - job.updated_at).total_seconds()
    job.is_stale = age > _BROADCAST_JOB_STALE_AFTER_SECONDS
    return job


async def _new_broadcast_store() -> Redis:
    return Redis.from_url(redis_settings.REDIS_URL, decode_responses=True)


async def _write_job(redis: Redis, job: BroadcastJobStatus) -> None:
    _refresh_job_derived_fields(job)
    await redis.set(_job_key(job.job_id), job.model_dump_json())
    await redis.expire(_job_key(job.job_id), _BROADCAST_JOB_TTL_SECONDS)


async def _register_job(redis: Redis, job_id: str) -> None:
    await redis.lpush(_BROADCAST_JOB_LIST_KEY, job_id)
    await redis.ltrim(_BROADCAST_JOB_LIST_KEY, 0, _BROADCAST_JOB_LIST_MAX - 1)
    await redis.expire(_BROADCAST_JOB_LIST_KEY, _BROADCAST_JOB_TTL_SECONDS)


async def _remember_idempotency_key(redis: Redis, idempotency_key: str, job_id: str) -> None:
    await redis.set(_broadcast_idempotency_key(idempotency_key), job_id)
    await redis.expire(_broadcast_idempotency_key(idempotency_key), _BROADCAST_JOB_TTL_SECONDS)


async def _read_job_id_by_idempotency_key(redis: Redis, idempotency_key: str) -> Optional[str]:
    return await redis.get(_broadcast_idempotency_key(idempotency_key))


async def _read_job(redis: Redis, job_id: str) -> Optional[BroadcastJobStatus]:
    raw = await redis.get(_job_key(job_id))
    if not raw:
        return None
    job = BroadcastJobStatus.model_validate_json(raw)
    _refresh_job_derived_fields(job)
    _mark_job_staleness(job)
    return job


def _recipient_to_status(recipient: _Recipient) -> BroadcastRecipientStatus:
    return BroadcastRecipientStatus(
        user_id=str(recipient.user_id),
        telegram_id=recipient.telegram_id,
        tier_id=str(recipient.tier_id),
        tier_name=recipient.tier_name,
        onboarded_at=recipient.onboarded_at,
    )


def _build_recipient_fingerprint(recipients: list[_Recipient]) -> str:
    digest = hashlib.sha256()
    for user_id in sorted(str(recipient.user_id) for recipient in recipients):
        digest.update(user_id.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


async def _write_recipient_snapshot(redis: Redis, job_id: str, recipients: list[_Recipient]) -> None:
    order = [str(recipient.user_id) for recipient in recipients]
    await redis.set(_job_recipient_order_key(job_id), ",".join(order))
    await redis.expire(_job_recipient_order_key(job_id), _BROADCAST_JOB_TTL_SECONDS)
    if recipients:
        await redis.hset(
            _job_recipient_state_key(job_id),
            mapping={str(recipient.user_id): _recipient_to_status(recipient).model_dump_json() for recipient in recipients},
        )
    await redis.expire(_job_recipient_state_key(job_id), _BROADCAST_JOB_TTL_SECONDS)


async def _write_recipient_status(redis: Redis, job_id: str, recipient: BroadcastRecipientStatus) -> None:
    await redis.hset(_job_recipient_state_key(job_id), recipient.user_id, recipient.model_dump_json())
    await redis.expire(_job_recipient_state_key(job_id), _BROADCAST_JOB_TTL_SECONDS)


async def _read_recipient_status(redis: Redis, job_id: str, user_id: str) -> Optional[BroadcastRecipientStatus]:
    raw = await redis.hget(_job_recipient_state_key(job_id), user_id)
    if not raw:
        return None
    return BroadcastRecipientStatus.model_validate_json(raw)


async def _list_recipient_statuses(
    redis: Redis,
    job_id: str,
    *,
    offset: int,
    limit: int,
    status: Optional[str],
) -> BroadcastRecipientPage:
    raw_order = await redis.get(_job_recipient_order_key(job_id))
    if raw_order is None:
        raise HTTPException(status_code=404, detail="Job recipients not found")
    order = [item for item in raw_order.split(",") if item]
    rows = await redis.hmget(_job_recipient_state_key(job_id), order) if order else []
    recipients = [BroadcastRecipientStatus.model_validate_json(row) for row in rows if row]
    if status:
        recipients = [recipient for recipient in recipients if recipient.status == status]
    sliced = recipients[offset: offset + limit]
    return BroadcastRecipientPage(
        job_id=job_id,
        total=len(recipients),
        offset=offset,
        limit=limit,
        items=sliced,
    )


async def _list_jobs(redis: Redis, limit: int) -> list[BroadcastJobStatus]:
    job_ids = await redis.lrange(_BROADCAST_JOB_LIST_KEY, 0, max(limit - 1, 0))
    jobs: list[BroadcastJobStatus] = []
    for job_id in job_ids:
        job = await _read_job(redis, job_id)
        if job:
            jobs.append(job)
    return jobs


def _record_failure(
    job: BroadcastJobStatus,
    recipient: _Recipient,
    *,
    error_type: str,
    error_message: str,
) -> None:
    if len(job.failure_samples) >= _BROADCAST_FAILURE_SAMPLE_LIMIT:
        return
    job.failure_samples.append(
        BroadcastFailureSample(
            user_id=str(recipient.user_id),
            telegram_id=recipient.telegram_id,
            error_type=error_type,
            error_message=error_message[:400],
        )
    )


async def _get_optional_current_user(
    token: Optional[str] = Depends(_optional_oauth2_scheme),
    session: AsyncSession = Depends(get_session),
) -> Optional[AppUser]:
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id: str | None = payload.get("sub")
        if not user_id:
            return None
    except JWTError:
        return None
    result = await session.exec(select(AppUser).where(AppUser.id == user_id))
    return result.first()


def _parse_admin_allowlist() -> set[int]:
    raw = settings.BROADCAST_ADMIN_TELEGRAM_ALLOWLIST.strip()
    if not raw:
        return set()
    allowed: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        allowed.add(int(item))
    return allowed


def _check_admin_access(admin_token: Optional[str], current_user: Optional[AppUser]) -> None:
    if not settings.BROADCAST_ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="Broadcast admin token is not configured")
    if not admin_token or admin_token != settings.BROADCAST_ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")
    allowed_telegram_ids = _parse_admin_allowlist()
    if not allowed_telegram_ids:
        return
    if not current_user:
        raise HTTPException(status_code=401, detail="Authenticated admin user required")
    if current_user.telegram_id not in allowed_telegram_ids:
        raise HTTPException(status_code=403, detail="Admin user is not allowed")


def _validate_expected_fingerprint(expected: Optional[str], actual: str) -> None:
    if expected and expected != actual:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Recipient fingerprint mismatch",
                "expected_recipient_fingerprint": expected,
                "actual_recipient_fingerprint": actual,
            },
        )


def _job_cancel_requested(job: BroadcastJobStatus) -> bool:
    return job.status == "cancelling" or job.cancel_requested_at is not None


def _to_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


async def _select_recipients(session: AsyncSession, filters: BroadcastFilters, limit: Optional[int]) -> list[_Recipient]:
    now = datetime.now(UTC).replace(tzinfo=None)
    stmt = (
        select(
            AppUser.id,
            AppUser.telegram_id,
            UserSubscription.tier_id,
            UserSubscription.started_at,
            SubscriptionTier.name,
        )
        .join(UserSubscription, UserSubscription.user_id == AppUser.id)
        .join(SubscriptionTier, SubscriptionTier.id == UserSubscription.tier_id)
        .where(
            UserSubscription.status == SubscriptionStatus.active,
            or_(UserSubscription.expires_at.is_(None), UserSubscription.expires_at > now),
        )
        .order_by(UserSubscription.started_at.desc())
    )

    if filters.tier_ids:
        stmt = stmt.where(UserSubscription.tier_id.in_(filters.tier_ids))
    if filters.tier_names:
        stmt = stmt.where(SubscriptionTier.name.in_(filters.tier_names))
    if filters.onboarded_from:
        stmt = stmt.where(UserSubscription.started_at >= _to_utc_naive(filters.onboarded_from))
    if filters.onboarded_to:
        stmt = stmt.where(UserSubscription.started_at <= _to_utc_naive(filters.onboarded_to))
    if filters.has_sent_first_message is not None:
        stmt = stmt.where(AppUser.has_sent_first_message == filters.has_sent_first_message)
    if filters.campaigns:
        stmt = stmt.where(AppUser.campaign.in_(filters.campaigns))

    rows = (await session.exec(stmt)).all()

    recipients: list[_Recipient] = []
    seen_users: set[uuid.UUID] = set()
    for row in rows:
        user_id = row[0]
        if user_id in seen_users:
            continue
        seen_users.add(user_id)
        recipients.append(
            _Recipient(
                user_id=user_id,
                telegram_id=row[1],
                tier_id=row[2],
                onboarded_at=row[3],
                tier_name=row[4],
            )
        )

    if filters.active_within_days:
        window_start = now - timedelta(days=filters.active_within_days)
        activity_stmt = (
            select(RequestLedger.user_id)
            .where(
                RequestLedger.user_id.in_([r.user_id for r in recipients]),
                RequestLedger.feature == "text",
                RequestLedger.created_at >= window_start,
            )
            .group_by(RequestLedger.user_id)
        )
        active_ids = set((await session.exec(activity_stmt)).all())
        recipients = [r for r in recipients if r.user_id in active_ids]

    if limit:
        recipients = recipients[:limit]

    return recipients


async def _broadcast_worker(
    *,
    job_id: str,
    text: str,
    recipients: list[_Recipient],
    per_second: float,
) -> None:
    delay_s = 1.0 / per_second
    bot = Bot(token=settings.BOT_TOKEN)
    redis = await _new_broadcast_store()
    job = await _read_job(redis, job_id)
    if not job:
        await bot.session.close()
        await redis.aclose()
        return
    if _job_cancel_requested(job):
        now = datetime.now(UTC)
        job.status = "cancelled"
        job.cancelled_at = now
        job.finished_at = now
        job.updated_at = now
        await _write_job(redis, job)
        await bot.session.close()
        await redis.aclose()
        return
    job.status = "running"
    job.updated_at = datetime.now(UTC)
    await _write_job(redis, job)

    try:
        for recipient in recipients:
            latest_job = await _read_job(redis, job_id)
            if not latest_job:
                break
            job = latest_job
            if _job_cancel_requested(job):
                now = datetime.now(UTC)
                job.status = "cancelled"
                job.cancelled_at = now
                job.finished_at = now
                job.updated_at = now
                await _write_job(redis, job)
                return
            recipient_status = await _read_recipient_status(redis, job_id, str(recipient.user_id)) or _recipient_to_status(recipient)
            job.last_attempted_user_id = str(recipient.user_id)
            job.last_attempted_telegram_id = recipient.telegram_id
            job.attempted += 1
            job.updated_at = datetime.now(UTC)
            recipient_status.attempts += 1
            recipient_status.attempted_at = job.updated_at
            try:
                await bot.send_message(chat_id=recipient.telegram_id, text=text)
                job.sent += 1
                recipient_status.status = "sent"
                recipient_status.delivered_at = datetime.now(UTC)
                recipient_status.error_type = None
                recipient_status.error_message = None
            except TelegramRetryAfter as e:
                job.retried += 1
                await asyncio.sleep(float(getattr(e, "retry_after", 1.0)))
                recipient_status.attempts += 1
                recipient_status.attempted_at = datetime.now(UTC)
                try:
                    await bot.send_message(chat_id=recipient.telegram_id, text=text)
                    job.sent += 1
                    job.retry_succeeded += 1
                    recipient_status.status = "sent"
                    recipient_status.delivered_at = datetime.now(UTC)
                    recipient_status.error_type = None
                    recipient_status.error_message = None
                except Exception as inner_exc:
                    job.failed += 1
                    job.last_error = str(inner_exc)
                    recipient_status.status = "failed"
                    recipient_status.error_type = type(inner_exc).__name__
                    recipient_status.error_message = str(inner_exc)[:400]
                    _record_failure(
                        job,
                        recipient,
                        error_type=type(inner_exc).__name__,
                        error_message=str(inner_exc),
                    )
            except (TelegramForbiddenError, TelegramBadRequest) as e:
                job.failed += 1
                job.last_error = str(e)
                recipient_status.status = "failed"
                recipient_status.error_type = type(e).__name__
                recipient_status.error_message = str(e)[:400]
                if isinstance(e, TelegramForbiddenError):
                    job.forbidden += 1
                else:
                    job.bad_request += 1
                _record_failure(
                    job,
                    recipient,
                    error_type=type(e).__name__,
                    error_message=str(e),
                )
            except Exception as e:
                job.failed += 1
                job.last_error = str(e)
                recipient_status.status = "failed"
                recipient_status.error_type = type(e).__name__
                recipient_status.error_message = str(e)[:400]
                _record_failure(
                    job,
                    recipient,
                    error_type=type(e).__name__,
                    error_message=str(e),
                )

            await _write_job(redis, job)
            await _write_recipient_status(redis, job_id, recipient_status)
            await asyncio.sleep(delay_s)

        if job.status not in {"cancelled", "cancelling"}:
            job.status = "completed"
    except Exception as e:
        job.status = "failed"
        job.last_error = str(e)
    finally:
        now = datetime.now(UTC)
        job.updated_at = now
        if job.status in {"completed", "failed", "cancelled"}:
            job.finished_at = now
        if job.status == "cancelled" and job.cancelled_at is None:
            job.cancelled_at = now
        await _write_job(redis, job)
        await bot.session.close()
        await redis.aclose()


@admin_broadcast.get("/panel", response_class=HTMLResponse)
async def broadcast_panel() -> str:
    return """<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Broadcast Panel</title>
  <style>
    body { font-family: sans-serif; max-width: 900px; margin: 24px auto; padding: 0 12px; }
    textarea, input, select { width: 100%; margin: 6px 0 14px; padding: 8px; box-sizing: border-box; }
    button { margin-right: 8px; padding: 8px 12px; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    pre { background: #111; color: #eee; padding: 12px; overflow: auto; }
  </style>
</head>
<body>
  <h2>Admin Broadcast</h2>
  <label>Admin token</label>
  <input id="token" type="password" placeholder="BROADCAST_ADMIN_TOKEN" />
  <label>Message</label>
  <textarea id="text" rows="8" placeholder="Message to users"></textarea>
  <div class="row">
    <div><label>Tier names (comma)</label><input id="tierNames" placeholder="Welcoming Bonus" /></div>
    <div><label>Campaigns (comma)</label><input id="campaigns" placeholder="organic,ads_1" /></div>
  </div>
  <div class="row">
    <div><label>Onboarded from (ISO)</label><input id="from" placeholder="2026-05-05T00:00:00Z" /></div>
    <div><label>Onboarded to (ISO)</label><input id="to" placeholder="2026-05-06T00:00:00Z" /></div>
  </div>
  <div class="row">
    <div><label>Has sent first message</label>
      <select id="hasFirst">
        <option value="">Any</option>
        <option value="true">true</option>
        <option value="false">false</option>
      </select>
    </div>
    <div><label>Active within days</label><input id="activeDays" type="number" min="1" max="365" placeholder="7" /></div>
  </div>
  <div class="row">
    <div><label>Rate (msg/s)</label><input id="perSecond" type="number" step="0.5" value="15" /></div>
    <div><label>Limit recipients</label><input id="limit" type="number" min="1" placeholder="optional" /></div>
  </div>
  <div class="row">
    <div><label>Expected fingerprint</label><input id="fingerprint" placeholder="Paste preview fingerprint to confirm audience" /></div>
    <div><label>Idempotency key</label><input id="idempotencyKey" placeholder="optional client-generated key" /></div>
  </div>
  <button onclick="preview()">Preview</button>
  <button onclick="send()">Send</button>
  <button onclick="cancelJob()">Cancel job</button>
  <button onclick="checkJob()">Check job</button>
  <label>Job ID</label>
  <input id="jobId" placeholder="Paste a job id to resume polling" />
  <pre id="out"></pre>
  <script>
    let pollTimer = null;

    function payload() {
      const hasFirst = document.getElementById('hasFirst').value;
      const activeDays = document.getElementById('activeDays').value;
      const limit = document.getElementById('limit').value;
      return {
        text: document.getElementById('text').value,
        per_second: Number(document.getElementById('perSecond').value || 15),
        limit: limit ? Number(limit) : null,
        expected_recipient_fingerprint: document.getElementById('fingerprint').value || null,
        idempotency_key: document.getElementById('idempotencyKey').value || null,
        filters: {
          tier_names: document.getElementById('tierNames').value.split(',').map(s => s.trim()).filter(Boolean),
          campaigns: document.getElementById('campaigns').value.split(',').map(s => s.trim()).filter(Boolean),
          onboarded_from: document.getElementById('from').value || null,
          onboarded_to: document.getElementById('to').value || null,
          has_sent_first_message: hasFirst === '' ? null : (hasFirst === 'true'),
          active_within_days: activeDays ? Number(activeDays) : null
        }
      };
    }
    function headers() {
      return {
        'Content-Type': 'application/json',
        'X-Admin-Token': document.getElementById('token').value
      };
    }
    function renderJob(data) {
      document.getElementById('out').textContent = JSON.stringify(data, null, 2);
      if (data && data.job_id) document.getElementById('jobId').value = data.job_id;
      if (data && data.recipient_fingerprint) document.getElementById('fingerprint').value = data.recipient_fingerprint;
    }
    async function call(path) {
      const r = await fetch(path, {
        method: 'POST',
        headers: headers(),
        body: JSON.stringify(payload())
      });
      const txt = await r.text();
      try { return JSON.parse(txt); }
      catch { return { raw: txt, status: r.status }; }
    }
    async function fetchJob(jobId) {
      const r = await fetch(`/api/v1/admin/broadcast/jobs/${encodeURIComponent(jobId)}`, {
        headers: { 'X-Admin-Token': document.getElementById('token').value }
      });
      const txt = await r.text();
      try { return JSON.parse(txt); }
      catch { return { raw: txt, status: r.status }; }
    }
    async function preview() { renderJob(await call('/api/v1/admin/broadcast/preview')); }
    async function send() {
      const data = await call('/api/v1/admin/broadcast/jobs');
      renderJob(data);
      if (data && data.job_id) pollJob(data.job_id);
    }
    async function checkJob() {
      const jobId = document.getElementById('jobId').value.trim();
      if (!jobId) return;
      renderJob(await fetchJob(jobId));
      pollJob(jobId);
    }
    async function cancelJob() {
      const jobId = document.getElementById('jobId').value.trim();
      if (!jobId) return;
      const r = await fetch(`/api/v1/admin/broadcast/jobs/${encodeURIComponent(jobId)}/cancel`, {
        method: 'POST',
        headers: { 'X-Admin-Token': document.getElementById('token').value }
      });
      const txt = await r.text();
      try { renderJob(JSON.parse(txt)); }
      catch { renderJob({ raw: txt, status: r.status }); }
    }
    async function pollJob(jobId) {
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = setInterval(async () => {
        const data = await fetchJob(jobId);
        renderJob(data);
        if (!data || !data.status || ['completed', 'failed'].includes(data.status)) {
          clearInterval(pollTimer);
          pollTimer = null;
        }
      }, 1500);
    }
  </script>
</body>
</html>"""


@admin_broadcast.post("/preview", response_model=BroadcastPreviewResponse)
async def preview_broadcast(
    req: BroadcastRequest,
    session: AsyncSession = Depends(get_session),
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    current_user: Optional[AppUser] = Depends(_get_optional_current_user),
) -> BroadcastPreviewResponse:
    _check_admin_access(x_admin_token, current_user)
    recipients = await _select_recipients(session, req.filters, req.limit)
    return BroadcastPreviewResponse(
        recipients=len(recipients),
        recipient_fingerprint=_build_recipient_fingerprint(recipients),
        sample=[_recipient_to_status(recipient) for recipient in recipients[:20]],
    )


async def _enqueue_broadcast_job(
    req: BroadcastRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession,
    redis: Redis,
    x_admin_token: Optional[str],
    current_user: Optional[AppUser],
) -> BroadcastSendResponse:
    _check_admin_access(x_admin_token, current_user)
    if req.idempotency_key:
        existing_job_id = await _read_job_id_by_idempotency_key(redis, req.idempotency_key)
        if existing_job_id:
            existing_job = await _read_job(redis, existing_job_id)
            if existing_job:
                return BroadcastSendResponse(
                    job_id=existing_job.job_id,
                    recipients=existing_job.total,
                    status=existing_job.status,
                    poll_url=f"/api/v1/admin/broadcast/jobs/{existing_job.job_id}",
                    recipients_url=f"/api/v1/admin/broadcast/jobs/{existing_job.job_id}/recipients",
                    attempted=existing_job.attempted,
                    sent=existing_job.sent,
                    failed=existing_job.failed,
                    pending=existing_job.pending,
                    updated_at=existing_job.updated_at,
                    idempotency_key=existing_job.idempotency_key,
                    note=existing_job.note,
                )
    recipients = await _select_recipients(session, req.filters, req.limit)

    if not recipients:
        raise HTTPException(status_code=400, detail="No recipients matched filters")

    recipient_fingerprint = _build_recipient_fingerprint(recipients)
    _validate_expected_fingerprint(req.expected_recipient_fingerprint, recipient_fingerprint)

    job_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    job = BroadcastJobStatus(
        job_id=job_id,
        status="queued",
        total=len(recipients),
        filters=req.filters,
        per_second=req.per_second,
        requested_limit=req.limit,
        text_preview=req.text[:200],
        recipient_fingerprint=recipient_fingerprint,
        attempted=0,
        sent=0,
        failed=0,
        pending=len(recipients),
        started_at=now,
        updated_at=now,
        runner_id=os.getenv("HOSTNAME"),
        created_by_user_id=str(current_user.id) if current_user else None,
        created_by_telegram_id=current_user.telegram_id if current_user else None,
        expected_recipient_fingerprint=req.expected_recipient_fingerprint,
        idempotency_key=req.idempotency_key,
    )
    await _write_job(redis, job)
    await _write_recipient_snapshot(redis, job_id, recipients)
    await _register_job(redis, job_id)
    if req.idempotency_key:
        await _remember_idempotency_key(redis, req.idempotency_key, job_id)

    background_tasks.add_task(
        _broadcast_worker,
        job_id=job_id,
        text=req.text,
        recipients=recipients,
        per_second=req.per_second,
    )

    return BroadcastSendResponse(
        job_id=job_id,
        recipients=len(recipients),
        status=job.status,
        poll_url=f"/api/v1/admin/broadcast/jobs/{job_id}",
        recipients_url=f"/api/v1/admin/broadcast/jobs/{job_id}/recipients",
        attempted=job.attempted,
        sent=job.sent,
        failed=job.failed,
        pending=job.pending,
        updated_at=job.updated_at,
        idempotency_key=job.idempotency_key,
        note=job.note,
    )


@admin_broadcast.post("/send", response_model=BroadcastSendResponse)
async def send_broadcast(
    req: BroadcastRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    current_user: Optional[AppUser] = Depends(_get_optional_current_user),
) -> BroadcastSendResponse:
    return await _enqueue_broadcast_job(req, background_tasks, session, redis, x_admin_token, current_user)


@admin_broadcast.post("/jobs", response_model=BroadcastSendResponse)
async def create_broadcast_job(
    req: BroadcastRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    current_user: Optional[AppUser] = Depends(_get_optional_current_user),
) -> BroadcastSendResponse:
    return await _enqueue_broadcast_job(req, background_tasks, session, redis, x_admin_token, current_user)


@admin_broadcast.get("/jobs", response_model=BroadcastJobListResponse)
async def list_broadcast_jobs(
    limit: int = 20,
    redis: Redis = Depends(get_redis),
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    current_user: Optional[AppUser] = Depends(_get_optional_current_user),
) -> BroadcastJobListResponse:
    _check_admin_access(x_admin_token, current_user)
    return BroadcastJobListResponse(items=await _list_jobs(redis, min(max(limit, 1), 100)))


@admin_broadcast.get("/jobs/{job_id}", response_model=BroadcastJobStatus)
async def get_broadcast_job(
    job_id: str,
    redis: Redis = Depends(get_redis),
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    current_user: Optional[AppUser] = Depends(_get_optional_current_user),
) -> BroadcastJobStatus:
    _check_admin_access(x_admin_token, current_user)
    job = await _read_job(redis, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@admin_broadcast.get("/jobs/{job_id}/recipients", response_model=BroadcastRecipientPage)
async def get_broadcast_job_recipients(
    job_id: str,
    offset: int = 0,
    limit: int = 100,
    status: Optional[str] = None,
    redis: Redis = Depends(get_redis),
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    current_user: Optional[AppUser] = Depends(_get_optional_current_user),
) -> BroadcastRecipientPage:
    _check_admin_access(x_admin_token, current_user)
    job = await _read_job(redis, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return await _list_recipient_statuses(
        redis,
        job_id,
        offset=max(offset, 0),
        limit=min(max(limit, 1), 500),
        status=status,
    )


@admin_broadcast.post("/jobs/{job_id}/cancel", response_model=BroadcastCancelResponse)
async def cancel_broadcast_job(
    job_id: str,
    redis: Redis = Depends(get_redis),
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    current_user: Optional[AppUser] = Depends(_get_optional_current_user),
) -> BroadcastCancelResponse:
    _check_admin_access(x_admin_token, current_user)
    job = await _read_job(redis, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    now = datetime.now(UTC)
    if job.status in {"completed", "failed", "cancelled"}:
        return BroadcastCancelResponse(
            job_id=job.job_id,
            status=job.status,
            cancel_requested_at=job.cancel_requested_at,
            cancelled_at=job.cancelled_at,
            updated_at=job.updated_at,
            note="Job is already terminal",
        )
    job.status = "cancelling"
    job.cancel_requested_at = now
    job.updated_at = now
    await _write_job(redis, job)
    return BroadcastCancelResponse(
        job_id=job.job_id,
        status=job.status,
        cancel_requested_at=job.cancel_requested_at,
        cancelled_at=job.cancelled_at,
        updated_at=job.updated_at,
        note="Cancellation requested; the worker stops before the next unsent recipient",
    )
