from __future__ import annotations

from collections.abc import Mapping
from datetime import timezone
from typing import Any

from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import WorkRun, WorkRunActivityEvent, utcnow_naive
from app.schemas.work_runs import WorkRunActivityEventResponse


_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


def _clean_text(value: str | None, *, limit: int) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized[:limit] or None


def activity_response(event: WorkRunActivityEvent) -> WorkRunActivityEventResponse:
    return WorkRunActivityEventResponse(
        id=event.id,
        sequence=event.sequence,
        kind=event.kind,
        status=event.status,
        phase=event.phase,
        title=event.title,
        detail=event.detail,
        metadata=event.event_metadata,
        started_at=event.started_at.replace(tzinfo=timezone.utc),
        completed_at=(
            event.completed_at.replace(tzinfo=timezone.utc)
            if event.completed_at
            else None
        ),
    )


async def list_activity_events(
    session: AsyncSession,
    work_run_id: Any,
) -> list[WorkRunActivityEvent]:
    return list(
        (
            await session.exec(
                select(WorkRunActivityEvent)
                .where(WorkRunActivityEvent.work_run_id == work_run_id)
                .order_by(WorkRunActivityEvent.sequence)
            )
        ).all()
    )


async def record_activity_event(
    session: AsyncSession,
    run: WorkRun,
    *,
    event_key: str,
    kind: str,
    status: str,
    phase: str | None = None,
    title: str | None = None,
    detail: str | None = None,
    metadata: Mapping[str, object] | None = None,
) -> WorkRunActivityEvent:
    event = (
        await session.exec(
            select(WorkRunActivityEvent).where(
                WorkRunActivityEvent.work_run_id == run.id,
                WorkRunActivityEvent.event_key == event_key,
            )
        )
    ).first()
    now = utcnow_naive()
    if event is None:
        highest_sequence = (
            await session.exec(
                select(func.coalesce(func.max(WorkRunActivityEvent.sequence), 0)).where(
                    WorkRunActivityEvent.work_run_id == run.id
                )
            )
        ).one()
        event = WorkRunActivityEvent(
            work_run_id=run.id,
            sequence=int(highest_sequence) + 1,
            event_key=event_key[:128],
            kind=kind[:32],
            status=status[:24],
            phase=phase[:32] if phase else None,
            title=_clean_text(title, limit=240),
            detail=_clean_text(detail, limit=1000),
            event_metadata=dict(metadata or {}),
            started_at=now,
            completed_at=now if status in _TERMINAL_STATUSES else None,
        )
    else:
        event.kind = kind[:32]
        event.status = status[:24]
        event.phase = phase[:32] if phase else event.phase
        if title is not None:
            event.title = _clean_text(title, limit=240)
        if detail is not None:
            event.detail = _clean_text(detail, limit=1000)
        if metadata is not None:
            event.event_metadata = dict(metadata)
        if status in _TERMINAL_STATUSES:
            event.completed_at = event.completed_at or now
    session.add(event)
    await session.flush()
    return event


async def finish_active_activity_events(
    session: AsyncSession,
    run: WorkRun,
    *,
    status: str = "completed",
) -> list[WorkRunActivityEvent]:
    events = list(
        (
            await session.exec(
                select(WorkRunActivityEvent).where(
                    WorkRunActivityEvent.work_run_id == run.id,
                    WorkRunActivityEvent.status == "active",
                )
            )
        ).all()
    )
    now = utcnow_naive()
    for event in events:
        event.status = status
        event.completed_at = now
        session.add(event)
    return events
