import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.db.database import get_session
from app.db.models import AppUser, RequestLedger
from app.db.subscription_tiers import SubscriptionStatus, SubscriptionTier, UserSubscription


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


class BroadcastSendResponse(BaseModel):
    job_id: str
    recipients: int
    status: str


class BroadcastJobStatus(BaseModel):
    job_id: str
    status: str
    total: int
    sent: int
    failed: int
    started_at: datetime
    finished_at: Optional[datetime] = None
    last_error: Optional[str] = None


_broadcast_jobs: dict[str, BroadcastJobStatus] = {}


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
    job = _broadcast_jobs[job_id]

    try:
        for recipient in recipients:
            try:
                await bot.send_message(chat_id=recipient.telegram_id, text=text)
                job.sent += 1
            except TelegramRetryAfter as e:
                await asyncio.sleep(float(getattr(e, "retry_after", 1.0)))
                try:
                    await bot.send_message(chat_id=recipient.telegram_id, text=text)
                    job.sent += 1
                except Exception as inner_exc:
                    job.failed += 1
                    job.last_error = str(inner_exc)
            except (TelegramForbiddenError, TelegramBadRequest) as e:
                job.failed += 1
                job.last_error = str(e)
            except Exception as e:
                job.failed += 1
                job.last_error = str(e)

            await asyncio.sleep(delay_s)

        job.status = "completed"
    except Exception as e:
        job.status = "failed"
        job.last_error = str(e)
    finally:
        job.finished_at = datetime.now(UTC)
        await bot.session.close()


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
  <pre id="out"></pre>
  <script>
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
    async function call(path) {
      const r = await fetch(path, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Admin-Token': document.getElementById('token').value
        },
        body: JSON.stringify(payload())
      });
      const txt = await r.text();
      try { return JSON.stringify(JSON.parse(txt), null, 2); }
      catch { return txt; }
    }
    async function preview() { document.getElementById('out').textContent = await call('/api/v1/admin/broadcast/preview'); }
    async function send() { document.getElementById('out').textContent = await call('/api/v1/admin/broadcast/send'); }
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
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
) -> BroadcastSendResponse:
    _check_admin_token(x_admin_token)
    recipients = await _select_recipients(session, req.filters, req.limit)

    if not recipients:
        raise HTTPException(status_code=400, detail="No recipients matched filters")

    job_id = str(uuid.uuid4())
    _broadcast_jobs[job_id] = BroadcastJobStatus(
        job_id=job_id,
        status="running",
        total=len(recipients),
        sent=0,
        failed=0,
        started_at=datetime.now(UTC),
    )

    background_tasks.add_task(
        _broadcast_worker,
        job_id=job_id,
        text=req.text,
        recipients=recipients,
        per_second=req.per_second,
    )

    return BroadcastSendResponse(job_id=job_id, recipients=len(recipients), status="running")


@admin_broadcast.get("/jobs/{job_id}", response_model=BroadcastJobStatus)
async def get_broadcast_job(
    job_id: str,
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
) -> BroadcastJobStatus:
    _check_admin_token(x_admin_token)
    job = _broadcast_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
