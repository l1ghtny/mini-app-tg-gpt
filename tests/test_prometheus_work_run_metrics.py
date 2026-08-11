import pytest
from prometheus_client import generate_latest

from app.core.prometheus import record_internal_event, record_internal_value


@pytest.fixture
def rebuild_test_db() -> None:
    """Pure metric tests do not need the repository-wide database fixture."""


def test_work_run_metrics_use_bounded_operational_labels() -> None:
    tags = {
        "kind": "spreadsheet_builder_xlsx",
        "kind_version": 1,
        "event_type": "work.done",
        "status": "succeeded",
        "stage": "completed",
        "error_code": None,
        "user_id": "must-not-be-exported",
        "work_run_id": "must-not-be-exported",
    }

    record_internal_event("work.run.lifecycle", tags)
    record_internal_value("work.run.total_duration", 12.5, tags)
    output = generate_latest().decode()

    lifecycle_lines = [
        line for line in output.splitlines() if line.startswith("lightny_work_run_")
    ]
    assert any("lightny_work_run_lifecycle_total" in line for line in lifecycle_lines)
    assert any("lightny_work_run_total_duration_seconds" in line for line in lifecycle_lines)
    assert all("user_id" not in line for line in lifecycle_lines)
    assert all("work_run_id" not in line for line in lifecycle_lines)


def test_unknown_internal_metrics_are_not_exported_to_prometheus() -> None:
    record_internal_event(
        "frontend.arbitrary_event",
        {"user_id": "high-cardinality-value"},
    )

    assert "frontend_arbitrary_event" not in generate_latest().decode()


def test_work_quality_metrics_export_only_bounded_dimensions() -> None:
    quality_tags = {
        "kind": "agentic_task",
        "kind_version": 1,
        "validation_passed": "true",
        "artifact_contract_passed": "true",
        "work_run_id": "must-not-be-exported",
    }
    value_tags = {
        "kind": "agentic_task",
        "kind_version": 1,
        "status": "succeeded",
        "error_code": None,
        "signal": "artifact_count",
        "work_run_id": "must-not-be-exported",
    }

    record_internal_event("work.run.quality_evaluated", quality_tags)
    record_internal_value("work.run.quality_value", 1, value_tags)
    record_internal_value(
        "work.run.tool_call_count",
        2,
        {**value_tags, "tool": "web_search"},
    )
    output = generate_latest().decode()

    assert "lightny_work_run_quality_evaluations_total" in output
    assert 'signal="artifact_count"' in output
    assert 'tool="web_search"' in output
    quality_lines = [
        line for line in output.splitlines() if "lightny_work_run_quality" in line
    ]
    assert all("work_run_id" not in line for line in quality_lines)
