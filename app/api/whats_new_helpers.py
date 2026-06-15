from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlparse

from sqlmodel import desc, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import AppUser, UserWhatsNewState, WhatsNewItem
from app.schemas.whats_new import (
    WhatsNewAudience,
    WhatsNewCTA,
    WhatsNewItemResponse,
    WhatsNewListResponse,
    WhatsNewSeenResponse,
)
from app.services.subscription_check.entitlements import get_current_subscription

DEFAULT_LIMIT = 5
MAX_LIMIT = 20
SUPPORTED_LANGS = {"en", "ru"}
SUPPORTED_PLANS = {"free", "pro"}


def _to_utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _now_utc_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _is_allowed_open_url(value: str | None) -> bool:
    if not value:
        return False

    raw = value.strip()
    lower = raw.lower()

    if lower.startswith("app://"):
        # Internal deep-link handled entirely by FE action dispatch.
        return len(raw) > len("app://")

    if lower.startswith("tg://"):
        parsed = urlparse(raw)
        return bool(parsed.scheme == "tg" and (parsed.netloc or parsed.path))

    if lower.startswith("t.me/") or lower.startswith("www.t.me/"):
        return len(raw.split("/", 1)) == 2 and bool(raw.split("/", 1)[1])

    if lower.startswith("http://") or lower.startswith("https://"):
        parsed = urlparse(raw)
        return bool(parsed.scheme in {"http", "https"} and parsed.netloc)

    return False


async def _get_user_plan(session: AsyncSession, current_user: AppUser) -> str:
    sub = await get_current_subscription(session, current_user.id)
    if sub and sub.tier and (sub.tier.price_cents or 0) > 0:
        return "pro"
    return "free"


def _build_cta(item: WhatsNewItem, lang: str) -> WhatsNewCTA | None:
    if not item.cta_kind:
        return None

    if item.cta_kind == "open_url" and not _is_allowed_open_url(item.cta_value):
        return None

    label = item.cta_label_ru if lang == "ru" and item.cta_label_ru else item.cta_label_en
    if not label:
        return None

    value = item.cta_value
    return WhatsNewCTA(label=label, kind=item.cta_kind, value=value)


def _is_visible_for_plan(item: WhatsNewItem, plan: str) -> bool:
    plans = [p for p in (item.audience_plans or []) if p in SUPPORTED_PLANS]
    if not plans:
        return True
    return plan in plans


def _to_item_response(item: WhatsNewItem, lang: str) -> WhatsNewItemResponse:
    title = item.title_ru if lang == "ru" and item.title_ru else item.title_en
    body = item.body_ru if lang == "ru" and item.body_ru else item.body_en
    audience = None
    plans = [p for p in (item.audience_plans or []) if p in SUPPORTED_PLANS]
    if plans or item.min_app_version:
        audience = WhatsNewAudience(plan=plans, min_app_version=item.min_app_version)

    return WhatsNewItemResponse(
        id=item.id,
        published_at=item.published_at,
        kind=item.kind,
        title=title,
        body=body,
        icon=item.icon,
        image_url=item.image_url,
        cta=_build_cta(item, lang),
        audience=audience,
        pinned=item.pinned,
    )


async def _resolve_seen_up_to(session: AsyncSession, user_id) -> datetime | None:
    state = await session.get(UserWhatsNewState, user_id)
    return state.seen_up_to if state else None


async def get_whats_new(
    *,
    session: AsyncSession,
    current_user: AppUser,
    lang: str,
    since: datetime | None,
    limit: int,
) -> WhatsNewListResponse:
    lang = lang if lang in SUPPORTED_LANGS else "en"
    limit = max(1, min(limit, MAX_LIMIT))
    since_dt = _to_utc_naive(since)
    now = _now_utc_naive()
    plan = await _get_user_plan(session, current_user)

    query = (
        select(WhatsNewItem)
        .where(
            WhatsNewItem.is_active == True,
            WhatsNewItem.published_at <= now,
            (WhatsNewItem.starts_at.is_(None)) | (WhatsNewItem.starts_at <= now),
            (WhatsNewItem.expires_at.is_(None)) | (WhatsNewItem.expires_at > now),
        )
        .order_by(
            desc(WhatsNewItem.pinned),
            desc(WhatsNewItem.published_at),
            desc(WhatsNewItem.id),
        )
    )
    rows = (await session.exec(query)).all()

    visible_rows = [row for row in rows if _is_visible_for_plan(row, plan)]
    latest_published_at = max((row.published_at for row in visible_rows), default=None)

    filtered_rows: list[WhatsNewItem] = []
    for row in visible_rows:
        if since_dt and not row.pinned and row.published_at <= since_dt:
            continue
        filtered_rows.append(row)
        if len(filtered_rows) >= limit:
            break

    seen_up_to = await _resolve_seen_up_to(session, current_user.id)
    unseen_rows = [row for row in visible_rows if seen_up_to is None or row.published_at > seen_up_to]

    return WhatsNewListResponse(
        items=[_to_item_response(row, lang) for row in filtered_rows],
        latest_published_at=latest_published_at,
        seen_up_to=seen_up_to,
        has_unseen=bool(unseen_rows),
        unseen_count=len(unseen_rows),
    )


async def mark_whats_new_seen(
    *,
    session: AsyncSession,
    current_user: AppUser,
    up_to: datetime | None,
    ids: list[str] | None,
) -> WhatsNewSeenResponse:
    resolved_up_to = _to_utc_naive(up_to)
    ids = ids or []

    if resolved_up_to is None and ids:
        rows = (await session.exec(
            select(WhatsNewItem.published_at).where(WhatsNewItem.id.in_(ids))
        )).all()
        if rows:
            resolved_up_to = max(rows)

    state = await session.get(UserWhatsNewState, current_user.id)
    if not state:
        state = UserWhatsNewState(user_id=current_user.id, seen_up_to=resolved_up_to)
        session.add(state)
        await session.commit()
        await session.refresh(state)
        return WhatsNewSeenResponse(seen_up_to=state.seen_up_to)

    if resolved_up_to and (state.seen_up_to is None or resolved_up_to > state.seen_up_to):
        state.seen_up_to = resolved_up_to
        session.add(state)
        await session.commit()
        await session.refresh(state)

    return WhatsNewSeenResponse(seen_up_to=state.seen_up_to)
