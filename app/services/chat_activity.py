from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import MessageActivityEvent


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
TOOL_KINDS = {
    "web_search": "web_search",
    "file_search": "file_search",
    "image_generation": "image_generation",
    "fetch_url": "fetch_url",
    "finance_search": "finance_search",
    "code_interpreter": "code_interpreter",
}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _provider_from_event(event: dict[str, Any]) -> str | None:
    provider = str(event.get("provider") or "").strip().lower()
    if provider:
        return provider[:32]
    source = str(event.get("source_event") or "").strip().lower()
    if source:
        return source.split(".", 1)[0][:32]
    return None


def _safe_sources(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []

    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in value[:24]:
        if isinstance(raw, str):
            url = raw.strip()
            title = ""
        elif isinstance(raw, dict):
            url = str(raw.get("url") or raw.get("uri") or "").strip()
            title = str(raw.get("title") or raw.get("name") or "").strip()
        else:
            continue
        parsed = urlparse(url)
        hostname = parsed.hostname
        if (
            parsed.scheme not in {"http", "https"}
            or not hostname
            or parsed.username
            or parsed.password
            or url in seen
        ):
            continue
        seen.add(url)
        source = {"url": url[:2000], "domain": hostname.lower().removeprefix("www.")[:255]}
        if title:
            source["title"] = title[:300]
        sources.append(source)
    return sources


def _merge_detail(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = {**(existing or {}), **incoming}
    old_sources = _safe_sources((existing or {}).get("sources"))
    new_sources = _safe_sources(incoming.get("sources"))
    if old_sources or new_sources:
        by_url = {source["url"]: source for source in [*old_sources, *new_sources]}
        merged["sources"] = list(by_url.values())[:24]
    return merged


def _serialize(event: MessageActivityEvent) -> dict[str, Any]:
    return {
        "type": "activity.upsert",
        "activity": {
            "id": str(event.id),
            "sequence": event.sequence,
            "event_key": event.event_key,
            "kind": event.kind,
            "status": event.status,
            "detail": event.detail or {},
            "started_at": event.started_at.isoformat() if event.started_at else None,
            "completed_at": event.completed_at.isoformat() if event.completed_at else None,
        },
    }


async def _upsert(
    session: AsyncSession,
    *,
    message_id: uuid.UUID,
    event_key: str,
    kind: str,
    status: str,
    detail: dict[str, Any] | None = None,
) -> MessageActivityEvent:
    existing = (
        await session.exec(
            select(MessageActivityEvent).where(
                MessageActivityEvent.message_id == message_id,
                MessageActivityEvent.event_key == event_key,
            )
        )
    ).first()
    now = _now()
    if existing:
        if not (existing.status in TERMINAL_STATUSES and status == "active"):
            existing.status = status
        existing.kind = kind
        existing.detail = _merge_detail(existing.detail or {}, detail or {})
        if existing.status in TERMINAL_STATUSES and existing.completed_at is None:
            existing.completed_at = now
        session.add(existing)
        return existing

    max_sequence = (
        await session.exec(
            select(func.max(MessageActivityEvent.sequence)).where(
                MessageActivityEvent.message_id == message_id
            )
        )
    ).one()
    activity = MessageActivityEvent(
        message_id=message_id,
        sequence=int(max_sequence or 0) + 1,
        event_key=event_key[:128],
        kind=kind[:48],
        status=status,
        detail=detail or {},
        completed_at=now if status in TERMINAL_STATUSES else None,
    )
    session.add(activity)
    return activity


async def _finish_active_events(
    session: AsyncSession,
    *,
    message_id: uuid.UUID,
    status: str,
    exclude: set[str] | None = None,
) -> list[MessageActivityEvent]:
    active = (
        await session.exec(
            select(MessageActivityEvent).where(
                MessageActivityEvent.message_id == message_id,
                MessageActivityEvent.status == "active",
            )
        )
    ).all()
    now = _now()
    touched: list[MessageActivityEvent] = []
    for activity in active:
        if activity.event_key in (exclude or set()):
            continue
        activity.status = status
        activity.completed_at = now
        session.add(activity)
        touched.append(activity)
    return touched


def _tool_kind(stage: str) -> str | None:
    normalized = stage.lower().replace(".", "_")
    for marker, kind in TOOL_KINDS.items():
        if marker in normalized:
            return kind
    if "tool" in normalized:
        return "tool"
    return None


def _tool_event_key(event: dict[str, Any], kind: str) -> str:
    activity_id = event.get("activity_id") or event.get("item_id") or event.get("call_id")
    index = event.get("index")
    suffix = activity_id if activity_id not in {None, ""} else index
    return f"tool:{kind}:{suffix if suffix not in {None, ''} else 'primary'}"[:128]


async def record_initial_activity(
    session: AsyncSession,
    *,
    message_id: uuid.UUID,
) -> list[dict[str, Any]]:
    activity = await _upsert(
        session,
        message_id=message_id,
        event_key="turn",
        kind="turn",
        status="active",
        detail={"stage": "preparing"},
    )
    await session.commit()
    return [_serialize(activity)]


async def record_stream_activity(
    session: AsyncSession,
    *,
    message_id: uuid.UUID,
    event: dict[str, Any],
    lifecycle: dict[str, Any],
) -> list[dict[str, Any]]:
    event_type = str(event.get("type") or "")
    touched: list[MessageActivityEvent] = []

    if event_type == "status":
        stage = str(event.get("stage") or event.get("phase") or "working").strip().lower()
        tool_kind = _tool_kind(stage)
        if tool_kind:
            status_raw = str(event.get("status") or "active").lower()
            status = "completed" if status_raw in {"done", "complete", "completed", "finished"} or "completed" in stage else "active"
            detail: dict[str, Any] = {"stage": stage}
            provider = _provider_from_event(event)
            if provider:
                detail["provider"] = provider
            touched.append(
                await _upsert(
                    session,
                    message_id=message_id,
                    event_key=_tool_event_key(event, tool_kind),
                    kind=tool_kind,
                    status=status,
                    detail=detail,
                )
            )
        elif stage in {"completed", "response.completed"}:
            touched.extend(
                await _finish_active_events(
                    session,
                    message_id=message_id,
                    status="completed",
                )
            )
            touched.append(
                await _upsert(
                    session,
                    message_id=message_id,
                    event_key="turn",
                    kind="turn",
                    status="completed",
                    detail={"stage": "completed"},
                )
            )
        else:
            if "retry" in stage:
                public_stage = "retrying"
            elif "think" in stage:
                public_stage = "thinking"
            else:
                public_stage = "working"
            detail = {"stage": public_stage}
            provider = _provider_from_event(event)
            if provider:
                detail["provider"] = provider
            touched.append(
                await _upsert(
                    session,
                    message_id=message_id,
                    event_key="turn",
                    kind="turn",
                    status="active",
                    detail=detail,
                )
            )

    elif event_type == "web_search.activity":
        detail = {
            "stage": "searching",
            "action": str(event.get("action") or "search")[:40],
            "sources": _safe_sources(event.get("sources")),
        }
        for key in ("query", "url", "pattern"):
            value = event.get(key)
            if isinstance(value, str) and value.strip():
                detail[key] = value.strip()[:1000]
        provider = _provider_from_event(event)
        if provider:
            detail["provider"] = provider
        touched.append(
            await _upsert(
                session,
                message_id=message_id,
                event_key=_tool_event_key(event, "web_search"),
                kind="web_search",
                status=str(event.get("status") or "completed"),
                detail=detail,
            )
        )

    elif event_type == "text.delta" and not lifecycle.get("activity_writing_started"):
        lifecycle["activity_writing_started"] = True
        touched.extend(
            await _finish_active_events(
                session,
                message_id=message_id,
                status="completed",
                exclude={"turn"},
            )
        )
        touched.append(
            await _upsert(
                session,
                message_id=message_id,
                event_key="response",
                kind="response",
                status="active",
                detail={"stage": "writing"},
            )
        )

    elif event_type in {"image.partial", "image.partial_url"}:
        touched.append(
            await _upsert(
                session,
                message_id=message_id,
                event_key=f"tool:image_generation:{event.get('index', 0)}",
                kind="image_generation",
                status="active",
                detail={"stage": "generating"},
            )
        )

    elif event_type == "image.ready":
        touched.append(
            await _upsert(
                session,
                message_id=message_id,
                event_key=f"tool:image_generation:{event.get('index', 0)}",
                kind="image_generation",
                status="completed",
                detail={"stage": "completed"},
            )
        )

    elif event_type == "file_search.used":
        touched.append(
            await _upsert(
                session,
                message_id=message_id,
                event_key="tool:file_search:primary",
                kind="file_search",
                status="completed",
                detail={"stage": "completed"},
            )
        )

    elif event_type == "done":
        touched.extend(
            await _finish_active_events(
                session,
                message_id=message_id,
                status="completed",
            )
        )
        touched.append(
            await _upsert(
                session,
                message_id=message_id,
                event_key="turn",
                kind="turn",
                status="completed",
                detail={"stage": "completed"},
            )
        )

    elif event_type == "error":
        touched.extend(
            await _finish_active_events(
                session,
                message_id=message_id,
                status="failed",
            )
        )
        touched.append(
            await _upsert(
                session,
                message_id=message_id,
                event_key="turn",
                kind="turn",
                status="failed",
                detail={"stage": "failed", "error_code": str(event.get("code") or "")[:80]},
            )
        )

    if not touched:
        return []

    await session.commit()
    unique = {activity.event_key: activity for activity in touched}
    return [_serialize(activity) for activity in sorted(unique.values(), key=lambda item: item.sequence)]
