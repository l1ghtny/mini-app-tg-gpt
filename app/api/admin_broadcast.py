import asyncio
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
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


class BroadcastPreviewResponse(BaseModel):
    recipients: int
    sample: list[dict]


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
    attempted: int
    sent: int
    failed: int
    pending: int
    updated_at: datetime
    note: str


class BroadcastJobStatus(BaseModel):
    job_id: str
    status: str
    total: int
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
    last_error: Optional[str] = None
    last_attempted_user_id: Optional[str] = None
    last_attempted_telegram_id: Optional[int] = None
    runner_id: Optional[str] = None
    note: str = "sent means Telegram accepted the message for delivery; it does not guarantee the user opened it"
    failure_samples: list[BroadcastFailureSample] = Field(default_factory=list)
    is_stale: bool = False


_BROADCAST_JOB_TTL_SECONDS = 7 * 24 * 60 * 60
_BROADCAST_JOB_STALE_AFTER_SECONDS = 120
_BROADCAST_FAILURE_SAMPLE_LIMIT = 20


def _job_key(job_id: str) -> str:
    return f"broadcast:job:{job_id}"


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


async def _read_job(redis: Redis, job_id: str) -> Optional[BroadcastJobStatus]:
    raw = await redis.get(_job_key(job_id))
    if not raw:
        return None
    job = BroadcastJobStatus.model_validate_json(raw)
    _refresh_job_derived_fields(job)
    _mark_job_staleness(job)
    return job


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


def _check_admin_token(admin_token: Optional[str]) -> None:
    if not settings.BROADCAST_ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="Broadcast admin token is not configured")
    if not admin_token or admin_token != settings.BROADCAST_ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")


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

    try:
        for recipient in recipients:
            job.last_attempted_user_id = str(recipient.user_id)
            job.last_attempted_telegram_id = recipient.telegram_id
            job.attempted += 1
            job.updated_at = datetime.now(UTC)
            try:
                await bot.send_message(chat_id=recipient.telegram_id, text=text)
                job.sent += 1
            except TelegramRetryAfter as e:
                job.retried += 1
                await asyncio.sleep(float(getattr(e, "retry_after", 1.0)))
                try:
                    await bot.send_message(chat_id=recipient.telegram_id, text=text)
                    job.sent += 1
                    job.retry_succeeded += 1
                except Exception as inner_exc:
                    job.failed += 1
                    job.last_error = str(inner_exc)
                    _record_failure(
                        job,
                        recipient,
                        error_type=type(inner_exc).__name__,
                        error_message=str(inner_exc),
                    )
            except (TelegramForbiddenError, TelegramBadRequest) as e:
                job.failed += 1
                job.last_error = str(e)
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
                _record_failure(
                    job,
                    recipient,
                    error_type=type(e).__name__,
                    error_message=str(e),
                )

            await _write_job(redis, job)
            await asyncio.sleep(delay_s)

        job.status = "completed"
    except Exception as e:
        job.status = "failed"
        job.last_error = str(e)
    finally:
        job.updated_at = datetime.now(UTC)
        job.finished_at = datetime.now(UTC)
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
  <button onclick="preview()">Preview</button>
  <button onclick="send()">Send</button>
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
      const data = await call('/api/v1/admin/broadcast/send');
      renderJob(data);
      if (data && data.job_id) pollJob(data.job_id);
    }
    async function checkJob() {
      const jobId = document.getElementById('jobId').value.trim();
      if (!jobId) return;
      renderJob(await fetchJob(jobId));
      pollJob(jobId);
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
) -> BroadcastPreviewResponse:
    _check_admin_token(x_admin_token)
    recipients = await _select_recipients(session, req.filters, req.limit)
    sample = [
        {
            "user_id": str(r.user_id),
            "telegram_id": r.telegram_id,
            "tier_id": str(r.tier_id),
            "tier_name": r.tier_name,
            "onboarded_at": r.onboarded_at.isoformat(),
        }
        for r in recipients[:20]
    ]
    return BroadcastPreviewResponse(recipients=len(recipients), sample=sample)


@admin_broadcast.post("/send", response_model=BroadcastSendResponse)
async def send_broadcast(
    req: BroadcastRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
) -> BroadcastSendResponse:
    _check_admin_token(x_admin_token)
    recipients = await _select_recipients(session, req.filters, req.limit)

    if not recipients:
        raise HTTPException(status_code=400, detail="No recipients matched filters")

    job_id = str(uuid.uuid4())
    job = BroadcastJobStatus(
        job_id=job_id,
        status="running",
        total=len(recipients),
        attempted=0,
        sent=0,
        failed=0,
        pending=len(recipients),
        started_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        runner_id=os.getenv("HOSTNAME"),
    )
    await _write_job(redis, job)

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
        attempted=job.attempted,
        sent=job.sent,
        failed=job.failed,
        pending=job.pending,
        updated_at=job.updated_at,
        note=job.note,
    )


@admin_broadcast.get("/jobs/{job_id}", response_model=BroadcastJobStatus)
async def get_broadcast_job(
    job_id: str,
    redis: Redis = Depends(get_redis),
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
) -> BroadcastJobStatus:
    _check_admin_token(x_admin_token)
    job = await _read_job(redis, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
