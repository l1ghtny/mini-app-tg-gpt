from __future__ import annotations

import os
import uuid
from datetime import datetime
from types import SimpleNamespace

os.environ.setdefault("R2_BUCKET", "test-public-bucket")
os.environ.setdefault("R2_ENDPOINT", "https://example.r2.cloudflarestorage.com")
os.environ.setdefault("R2_ACCESS_KEY_ID", "test-access-key")
os.environ.setdefault("R2_SECRET_ACCESS_KEY", "test-secret-key")

from app.db.models import WorkRunActivityEvent
from app.services.work_runs.activity import activity_response
from app.services.work_runs.agent_execution import _provider_activity_events


def test_activity_response_exposes_only_the_public_event_contract() -> None:
    event = WorkRunActivityEvent(
        id=uuid.uuid4(),
        work_run_id=uuid.uuid4(),
        sequence=3,
        event_key="tool:draft:1:web_search:1",
        kind="web_search",
        status="completed",
        phase="draft_1",
        detail="official supplier warranty terms",
        event_metadata={"count": 1},
        started_at=datetime(2026, 8, 15, 12, 0, 0),
        completed_at=datetime(2026, 8, 15, 12, 0, 1),
    )

    payload = activity_response(event).model_dump(mode="json")

    assert payload["sequence"] == 3
    assert payload["kind"] == "web_search"
    assert payload["metadata"] == {"count": 1}
    assert "event_key" not in payload
    assert "work_run_id" not in payload


def test_provider_activity_builds_safe_semantic_tool_events() -> None:
    response = SimpleNamespace(
        output=[
            SimpleNamespace(
                type="web_search_call",
                action=SimpleNamespace(query=" official   documentation ", queries=[]),
            ),
            SimpleNamespace(
                type="file_search_call",
                queries=["supplier warranty", "delivery terms"],
            ),
            SimpleNamespace(
                type="code_interpreter_call",
                code="secret = 'must not be persisted'",
            ),
        ]
    )

    events = _provider_activity_events(response)

    assert [event["kind"] for event in events] == [
        "web_search",
        "file_search",
        "code_interpreter",
    ]
    assert events[0]["detail"] == "official documentation"
    assert events[1]["metadata"] == {
        "count": 1,
        "queries": ["supplier warranty", "delivery terms"],
    }
    assert "secret" not in repr(events)


def test_activity_table_enforces_order_and_idempotency_per_run() -> None:
    constraints = {
        constraint.name for constraint in WorkRunActivityEvent.__table__.constraints
    }

    assert "uq_work_run_activity_sequence" in constraints
    assert "uq_work_run_activity_event_key" in constraints
