from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.services.work_runs import telemetry


def test_terminal_event_records_low_cardinality_lifecycle_and_durations(
    monkeypatch,
) -> None:
    counters: list[tuple[str, dict]] = []
    values: list[tuple[str, float, dict, str]] = []
    started_at = datetime(2026, 8, 7, 10, 0, 5)
    run = SimpleNamespace(
        id=uuid.uuid4(),
        kind="offer_comparison_xlsx",
        kind_version=1,
        status="succeeded",
        stage="completed",
        error_code=None,
        worker_id="worker-123",
        attempt_count=1,
        actual_cost_usd=Decimal("0.0123"),
        queued_at=started_at - timedelta(seconds=5),
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=12),
    )
    monkeypatch.setattr(
        telemetry,
        "track_internal_event",
        lambda key, tags: counters.append((key, tags)),
    )
    monkeypatch.setattr(
        telemetry,
        "track_internal_value",
        lambda key, value, tags, unit="none": values.append((key, value, tags, unit)),
    )

    telemetry.record_work_run_event(run, "work.done")

    assert counters == [("work.run.lifecycle", counters[0][1])]
    tags = counters[0][1]
    assert tags == {
        "kind": "offer_comparison_xlsx",
        "kind_version": 1,
        "event_type": "work.done",
        "status": "succeeded",
        "stage": "completed",
        "error_code": None,
    }
    assert "work_run_id" not in tags
    assert "worker_id" not in tags
    assert ("work.run.queue_duration", 5.0, tags, "second") in values
    assert ("work.run.execution_duration", 12.0, tags, "second") in values
    assert ("work.run.total_duration", 17.0, tags, "second") in values
    assert ("work.run.attempt_count", 1.0, tags, "none") in values
    assert ("work.run.actual_cost_usd", 0.0123, tags, "none") in values


def test_artifact_download_records_kind_and_size_without_identifiers(
    monkeypatch,
) -> None:
    counters: list[tuple[str, dict]] = []
    values: list[tuple[str, float, dict, str]] = []
    artifact = SimpleNamespace(
        kind="offer_comparison_xlsx",
        mime_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        status="ready",
        size_bytes=2048,
    )
    monkeypatch.setattr(
        telemetry,
        "track_internal_event",
        lambda key, tags: counters.append((key, tags)),
    )
    monkeypatch.setattr(
        telemetry,
        "track_internal_value",
        lambda key, value, tags, unit="none": values.append((key, value, tags, unit)),
    )

    telemetry.record_artifact_download(artifact)

    assert counters[0][0] == "work.artifact.download_url_created"
    assert values == [("work.artifact.download_size", 2048.0, counters[0][1], "byte")]


def test_completed_agent_result_records_bounded_quality_and_tool_metrics(
    monkeypatch,
) -> None:
    counters: list[tuple[str, dict]] = []
    values: list[tuple[str, float, dict, str]] = []
    completed_at = datetime(2026, 8, 12, 10, 0, 20)
    run = SimpleNamespace(
        id=uuid.uuid4(),
        kind="agentic_task",
        kind_version=1,
        status="succeeded",
        stage="completed",
        error_code=None,
        worker_id="worker-123",
        attempt_count=1,
        actual_cost_usd=Decimal("0.043"),
        queued_at=completed_at - timedelta(seconds=20),
        started_at=completed_at - timedelta(seconds=18),
        completed_at=completed_at,
        result_summary=json.dumps(
            {
                "quality": {
                    "validation_passed": True,
                    "artifact_requested": True,
                    "artifact_contract_passed": True,
                    "generation_attempts": 2,
                    "review_calls": 1,
                    "steering_restarts": 1,
                    "source_count": 3,
                    "citation_count": 5,
                    "artifact_count": 1,
                },
                "activity": {
                    "web_search_calls": 2,
                    "file_search_calls": 1,
                    "code_interpreter_calls": 1,
                },
            }
        ),
    )
    monkeypatch.setattr(
        telemetry,
        "track_internal_event",
        lambda key, tags: counters.append((key, tags)),
    )
    monkeypatch.setattr(
        telemetry,
        "track_internal_value",
        lambda key, value, tags, unit="none": values.append((key, value, tags, unit)),
    )

    telemetry.record_work_run_event(run, "work.done")

    quality_event = next(item for item in counters if item[0] == "work.run.quality_evaluated")
    assert quality_event[1] == {
        "kind": "agentic_task",
        "kind_version": 1,
        "validation_passed": "true",
        "artifact_contract_passed": "true",
    }
    quality_values = [item for item in values if item[0] == "work.run.quality_value"]
    assert {item[2]["signal"] for item in quality_values} == {
        "generation_attempts",
        "review_calls",
        "steering_restarts",
        "source_count",
        "citation_count",
        "artifact_count",
    }
    tool_values = [item for item in values if item[0] == "work.run.tool_call_count"]
    assert {item[2]["tool"] for item in tool_values} == {
        "web_search",
        "file_search",
        "code_interpreter",
    }
    assert all("work_run_id" not in item[2] for item in [*quality_values, *tool_values])
