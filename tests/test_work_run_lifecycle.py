from __future__ import annotations

import os
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

os.environ.setdefault("R2_BUCKET", "test-public-bucket")
os.environ.setdefault("R2_ENDPOINT", "https://example.r2.cloudflarestorage.com")
os.environ.setdefault("R2_ACCESS_KEY_ID", "test-access-key")
os.environ.setdefault("R2_SECRET_ACCESS_KEY", "test-secret-key")

from app.db.models import State
from app.schemas.work_runs import ReviseArtifactRequest
from app.services.work_runs import service
from app.services.work_runs.contracts import WorkRunErrorCode, WorkRunStatus


class _Result:
    def __init__(self, value: object | None = None) -> None:
        self.value = value

    def first(self) -> object | None:
        return self.value

    def all(self) -> list[object]:
        return [] if self.value is None else [self.value]


class _Session:
    def __init__(self, *, existing: object | None = None, ledger: object | None = None):
        self.existing = existing
        self.ledger = ledger
        self.added: list[object] = []
        self.commits = 0
        self.refresh_calls: list[dict[str, object]] = []

    async def exec(self, _statement: object) -> _Result:
        return _Result(self.existing)

    async def get(self, _model: object, _identifier: object) -> object | None:
        return self.ledger

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, _value: object, **kwargs: object) -> None:
        self.refresh_calls.append(kwargs)


def _source_run(status: WorkRunStatus = WorkRunStatus.FAILED) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        status=status.value,
        kind="offer_comparison_xlsx",
        kind_version=1,
        conversation_id=uuid.uuid4(),
        folder_id=uuid.uuid4(),
        input_manifest={"document_ids": [str(uuid.uuid4()), str(uuid.uuid4())]},
        instructions="Keep delivery terms visible",
        options={"currency": "EUR", "output_language": "en"},
        error_code=WorkRunErrorCode.INTERNAL_ERROR.value,
    )


@pytest.mark.asyncio
async def test_retry_clones_validated_manifest_and_records_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_run()
    retried = SimpleNamespace(id=uuid.uuid4())
    captured: dict[str, object] = {}

    async def fake_create_run(**kwargs: object) -> object:
        captured.update(kwargs)
        return retried

    retry_metric = Mock()
    monkeypatch.setattr(service, "create_run", fake_create_run)
    monkeypatch.setattr(service, "record_work_run_retry", retry_metric)

    result = await service.retry_run(
        session=_Session(),  # type: ignore[arg-type]
        user=SimpleNamespace(id=uuid.uuid4()),
        source=source,
        client_request_id="retry-request-1",
    )

    request = captured["request"]
    assert result is retried
    assert request.document_ids == [
        uuid.UUID(value) for value in source.input_manifest["document_ids"]
    ]
    assert request.options.model_dump() == {**source.options, "desired_columns": []}
    assert captured["retry_of_work_run_id"] == source.id
    assert captured["client_request_id"] == "retry-request-1"
    retry_metric.assert_called_once_with(source)


@pytest.mark.asyncio
async def test_retry_idempotency_key_cannot_be_reused_for_another_run() -> None:
    source = _source_run()
    existing = SimpleNamespace(
        input_manifest={"retry_of_work_run_id": str(uuid.uuid4())}
    )

    with pytest.raises(HTTPException) as error:
        await service.retry_run(
            session=_Session(existing=existing),  # type: ignore[arg-type]
            user=SimpleNamespace(id=uuid.uuid4()),
            source=source,
            client_request_id="reused-request-key",
        )

    assert error.value.status_code == 409
    assert error.value.detail == {"error_code": WorkRunErrorCode.INVALID_INPUT.value}


@pytest.mark.asyncio
async def test_retry_rejects_non_terminal_run() -> None:
    with pytest.raises(HTTPException) as error:
        await service.retry_run(
            session=_Session(),  # type: ignore[arg-type]
            user=SimpleNamespace(id=uuid.uuid4()),
            source=_source_run(WorkRunStatus.SUCCEEDED),
            client_request_id="retry-request-2",
        )

    assert error.value.status_code == 409
    assert error.value.detail == {
        "error_code": WorkRunErrorCode.RETRY_NOT_ALLOWED.value
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "run_status", [WorkRunStatus.VALIDATING, WorkRunStatus.STORING]
)
async def test_cancellation_is_rejected_after_point_of_no_return(
    run_status: WorkRunStatus,
) -> None:
    run = SimpleNamespace(status=run_status.value)

    with pytest.raises(HTTPException) as error:
        await service.cancel_run(_Session(), run)  # type: ignore[arg-type]

    assert error.value.status_code == 409
    assert error.value.detail == {"error_code": WorkRunErrorCode.CANCEL_TOO_LATE.value}


@pytest.mark.asyncio
async def test_queued_cancellation_refunds_reserved_allowance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = SimpleNamespace(state=State.reserved)
    run = SimpleNamespace(
        id=uuid.uuid4(),
        status=WorkRunStatus.QUEUED.value,
        stage="waiting_for_worker",
        cancelled_at=None,
        completed_at=None,
        lease_expires_at=datetime.now(),
        request_ledger_id=uuid.uuid4(),
    )
    session = _Session(ledger=ledger)
    lifecycle_metric = Mock()
    monkeypatch.setattr(service, "record_work_run_event", lifecycle_metric)
    monkeypatch.setattr(service, "finish_active_activity_events", AsyncMock())
    monkeypatch.setattr(service, "record_activity_event", AsyncMock())

    result = await service.cancel_run(session, run)  # type: ignore[arg-type]

    assert result.status == WorkRunStatus.CANCELLED.value
    assert result.stage == "cancelled"
    assert result.completed_at is not None
    assert result.lease_expires_at is None
    assert ledger.state == State.refunded
    assert session.commits == 1
    assert session.refresh_calls[0]["with_for_update"] is True
    lifecycle_metric.assert_called_once_with(run, "work.cancelled")


@pytest.mark.asyncio
async def test_worker_honors_cancellation_marker_across_status_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = SimpleNamespace(
        status=WorkRunStatus.VALIDATING.value,
        cancelled_at=datetime.now(),
    )
    complete = AsyncMock()
    monkeypatch.setattr(service, "complete_cancellation", complete)

    cancelled = await service._cancel_if_requested(
        session=_Session(),  # type: ignore[arg-type]
        redis=SimpleNamespace(),
        run=run,
    )

    assert cancelled is True
    complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_failure_preserves_specific_execution_error_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = SimpleNamespace(state=State.reserved)
    run = SimpleNamespace(
        id=uuid.uuid4(),
        status=WorkRunStatus.RUNNING.value,
        cancelled_at=None,
        stage="normalizing_data",
        error_code=None,
        error_message=None,
        completed_at=None,
        lease_expires_at=datetime.now(),
        request_ledger_id=uuid.uuid4(),
    )
    publish = AsyncMock()
    activity_event = SimpleNamespace()
    redis = SimpleNamespace()
    monkeypatch.setattr(service, "_publish", publish)
    monkeypatch.setattr(service, "finish_active_activity_events", AsyncMock())
    monkeypatch.setattr(
        service,
        "record_activity_event",
        AsyncMock(return_value=activity_event),
    )

    await service.fail_run(
        session=_Session(ledger=ledger),  # type: ignore[arg-type]
        redis=redis,
        run=run,
        error=service.WorkRunExecutionError(
            WorkRunErrorCode.PROVIDER_AMBIGUOUS,
            "provider result needs reconciliation",
        ),
    )

    assert run.status == WorkRunStatus.FAILED.value
    assert run.error_code == WorkRunErrorCode.PROVIDER_AMBIGUOUS.value
    assert ledger.state == State.refunded
    publish.assert_awaited_once_with(
        redis,
        run,
        "work.error",
        activity_event=activity_event,
    )


@pytest.mark.asyncio
async def test_artifact_revision_reuses_sources_and_records_immutable_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_run(WorkRunStatus.SUCCEEDED)
    artifact = SimpleNamespace(
        id=uuid.uuid4(),
        work_run_id=source.id,
        version=1,
        status="ready",
        deleted_at=None,
    )
    revised = SimpleNamespace(id=uuid.uuid4())
    create_run = AsyncMock(return_value=revised)
    monkeypatch.setattr(service, "owned_run", AsyncMock(return_value=source))
    monkeypatch.setattr(service, "create_run", create_run)

    result = await service.create_artifact_revision(
        session=_Session(),  # type: ignore[arg-type]
        user=SimpleNamespace(id=uuid.uuid4()),
        artifact=artifact,
        revision=ReviseArtifactRequest(instructions="Use shorter headers"),
        client_request_id="revision-request-1",
    )

    assert result is revised
    request = create_run.await_args.kwargs["request"]
    assert request.document_ids == [
        uuid.UUID(value) for value in source.input_manifest["document_ids"]
    ]
    assert "Keep delivery terms visible" in request.instructions
    assert "Use shorter headers" in request.instructions
    assert create_run.await_args.kwargs["revision_of_artifact_id"] == artifact.id
    assert create_run.await_args.kwargs["artifact_version"] == 2


@pytest.mark.asyncio
async def test_artifact_revision_rejects_non_successful_source_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_run(WorkRunStatus.FAILED)
    monkeypatch.setattr(service, "owned_run", AsyncMock(return_value=source))

    with pytest.raises(HTTPException) as error:
        await service.create_artifact_revision(
            session=_Session(),  # type: ignore[arg-type]
            user=SimpleNamespace(id=uuid.uuid4()),
            artifact=SimpleNamespace(
                id=uuid.uuid4(),
                work_run_id=source.id,
                version=1,
                status="ready",
                deleted_at=None,
            ),
            revision=ReviseArtifactRequest(instructions="Use shorter headers"),
            client_request_id="revision-request-2",
        )

    assert error.value.status_code == 409
    assert error.value.detail == {
        "error_code": WorkRunErrorCode.REVISION_NOT_ALLOWED.value
    }
