from __future__ import annotations

import logging
import os
import threading
from typing import Mapping

from prometheus_client import Counter, Histogram, start_http_server


logger = logging.getLogger(__name__)

_RUN_LABELS = (
    "kind",
    "kind_version",
    "event_type",
    "status",
    "stage",
    "error_code",
)
_RUN_VALUE_LABELS = ("kind", "kind_version", "status", "error_code")

WORK_RUN_LIFECYCLE = Counter(
    "lightny_work_run_lifecycle_total",
    "Lightny Work run lifecycle events.",
    _RUN_LABELS,
)
WORK_RUN_RETRIES = Counter(
    "lightny_work_run_retries_total",
    "User-requested Lightny Work run retries.",
    ("kind", "kind_version", "source_status", "source_error_code"),
)
WORK_RUN_QUEUE_DURATION = Histogram(
    "lightny_work_run_queue_duration_seconds",
    "Time a Lightny Work run spent waiting for a worker.",
    _RUN_VALUE_LABELS,
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600, 1800),
)
WORK_RUN_EXECUTION_DURATION = Histogram(
    "lightny_work_run_execution_duration_seconds",
    "Time a Lightny Work run spent executing.",
    _RUN_VALUE_LABELS,
    buckets=(1, 2, 5, 10, 30, 60, 120, 300, 600, 1200, 1800, 3600),
)
WORK_RUN_TOTAL_DURATION = Histogram(
    "lightny_work_run_total_duration_seconds",
    "Total time from queueing to terminal Lightny Work run state.",
    _RUN_VALUE_LABELS,
    buckets=(1, 2, 5, 10, 30, 60, 120, 300, 600, 1200, 1800, 3600),
)
WORK_RUN_ATTEMPT_COUNT = Histogram(
    "lightny_work_run_attempt_count",
    "Attempts used by a terminal Lightny Work run.",
    _RUN_VALUE_LABELS,
    buckets=(0, 1, 2, 3, 4, 5, 8, 13),
)
WORK_RUN_COST = Counter(
    "lightny_work_run_cost_usd_total",
    "Recorded provider cost for terminal Lightny Work runs in USD.",
    _RUN_VALUE_LABELS,
)
ARTIFACT_DOWNLOAD_URLS = Counter(
    "lightny_work_artifact_download_urls_created_total",
    "Authenticated artifact download URLs created by Lightny Work.",
    ("kind", "mime_type", "status"),
)
ARTIFACT_DOWNLOAD_BYTES = Counter(
    "lightny_work_artifact_download_bytes_total",
    "Artifact bytes represented by authenticated download URLs.",
    ("kind", "mime_type", "status"),
)

_EVENT_METRICS = {
    "work.run.lifecycle": (WORK_RUN_LIFECYCLE, _RUN_LABELS),
    "work.run.retry_requested": (
        WORK_RUN_RETRIES,
        ("kind", "kind_version", "source_status", "source_error_code"),
    ),
    "work.artifact.download_url_created": (
        ARTIFACT_DOWNLOAD_URLS,
        ("kind", "mime_type", "status"),
    ),
}
_VALUE_METRICS = {
    "work.run.queue_duration": (WORK_RUN_QUEUE_DURATION, _RUN_VALUE_LABELS),
    "work.run.execution_duration": (WORK_RUN_EXECUTION_DURATION, _RUN_VALUE_LABELS),
    "work.run.total_duration": (WORK_RUN_TOTAL_DURATION, _RUN_VALUE_LABELS),
    "work.run.attempt_count": (WORK_RUN_ATTEMPT_COUNT, _RUN_VALUE_LABELS),
    "work.run.actual_cost_usd": (WORK_RUN_COST, _RUN_VALUE_LABELS),
    "work.artifact.download_size": (
        ARTIFACT_DOWNLOAD_BYTES,
        ("kind", "mime_type", "status"),
    ),
}

_server_lock = threading.Lock()
_server_started = False


def _labels(tags: Mapping[str, object], names: tuple[str, ...]) -> dict[str, str]:
    return {name: str(tags.get(name) or "none") for name in names}


def record_internal_event(key: str, tags: Mapping[str, object]) -> None:
    metric_definition = _EVENT_METRICS.get(key)
    if metric_definition is None:
        return
    metric, label_names = metric_definition
    metric.labels(**_labels(tags, label_names)).inc()


def record_internal_value(
    key: str,
    value: float,
    tags: Mapping[str, object],
) -> None:
    metric_definition = _VALUE_METRICS.get(key)
    if metric_definition is None:
        return
    metric, label_names = metric_definition
    child = metric.labels(**_labels(tags, label_names))
    if isinstance(metric, Counter):
        child.inc(max(0.0, value))
    else:
        child.observe(value)


def start_metrics_server_from_env(service_name: str) -> None:
    raw_port = os.getenv("PROMETHEUS_METRICS_PORT", "").strip()
    if not raw_port:
        return
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError("PROMETHEUS_METRICS_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("PROMETHEUS_METRICS_PORT must be between 1 and 65535")

    global _server_started
    with _server_lock:
        if _server_started:
            return
        start_http_server(port)
        _server_started = True
    logger.info(
        "Prometheus metrics server started",
        extra={"metrics_port": port, "service_name": service_name},
    )
