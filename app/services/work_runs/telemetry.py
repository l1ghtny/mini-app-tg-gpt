from __future__ import annotations

import logging
from datetime import datetime

from app.core.metrics import track_internal_event, track_internal_value
from app.db.models import Artifact, WorkRun


logger = logging.getLogger(__name__)
_TERMINAL_EVENTS = frozenset({"work.done", "work.error", "work.cancelled"})


def _elapsed_seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return max(0.0, (end - start).total_seconds())


def record_work_run_event(run: WorkRun, event_type: str) -> None:
    tags = {
        "kind": getattr(run, "kind", "unknown"),
        "kind_version": getattr(run, "kind_version", 0),
        "event_type": event_type,
        "status": run.status,
        "stage": run.stage,
        "error_code": getattr(run, "error_code", None),
    }
    track_internal_event("work.run.lifecycle", tags)
    logger.info(
        "work-run lifecycle event",
        extra={
            "work_run_id": str(run.id),
            "work_run_kind": getattr(run, "kind", "unknown"),
            "work_run_status": run.status,
            "work_run_stage": run.stage,
            "work_run_event_type": event_type,
            "work_run_attempt_count": getattr(run, "attempt_count", 0),
            "work_run_error_code": getattr(run, "error_code", None),
            "work_run_worker_id": getattr(run, "worker_id", None),
        },
    )
    if event_type not in _TERMINAL_EVENTS:
        return

    queued_at = getattr(run, "queued_at", None)
    started_at = getattr(run, "started_at", None)
    completed_at = getattr(run, "completed_at", None)
    queue_seconds = _elapsed_seconds(queued_at, started_at)
    execution_seconds = _elapsed_seconds(started_at, completed_at)
    total_seconds = _elapsed_seconds(queued_at, completed_at)
    if queue_seconds is not None:
        track_internal_value("work.run.queue_duration", queue_seconds, tags, "second")
    if execution_seconds is not None:
        track_internal_value(
            "work.run.execution_duration",
            execution_seconds,
            tags,
            "second",
        )
    if total_seconds is not None:
        track_internal_value("work.run.total_duration", total_seconds, tags, "second")
    track_internal_value(
        "work.run.attempt_count",
        float(getattr(run, "attempt_count", 0)),
        tags,
    )
    track_internal_value(
        "work.run.actual_cost_usd",
        float(getattr(run, "actual_cost_usd", 0)),
        tags,
    )


def record_work_run_retry(source: WorkRun) -> None:
    track_internal_event(
        "work.run.retry_requested",
        {
            "kind": source.kind,
            "kind_version": source.kind_version,
            "source_status": source.status,
            "source_error_code": source.error_code,
        },
    )


def record_artifact_download(artifact: Artifact) -> None:
    tags = {
        "kind": artifact.kind,
        "mime_type": artifact.mime_type,
        "status": artifact.status,
    }
    track_internal_event("work.artifact.download_url_created", tags)
    track_internal_value(
        "work.artifact.download_size",
        float(artifact.size_bytes),
        tags,
        "byte",
    )
