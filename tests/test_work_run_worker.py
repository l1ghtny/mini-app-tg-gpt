from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.services.work_runs import worker
from app.services.work_runs.contracts import WorkRunErrorCode, WorkRunKind
from app.services.work_runs.service import WorkRunExecutionError


@pytest.fixture(autouse=True)
def rebuild_test_db() -> None:
    """This module exercises the worker loop with in-memory test doubles."""


class _ExpiringRun:
    def __init__(self) -> None:
        self._id = uuid.uuid4()
        self.kind = WorkRunKind.AGENTIC_TASK.value
        self.expired = False

    @property
    def id(self) -> uuid.UUID:
        if self.expired:
            raise AssertionError("expired ORM attributes must not be accessed")
        return self._id


class _Session:
    def __init__(self, run: _ExpiringRun) -> None:
        self.run = run
        self.rollback = AsyncMock(side_effect=self._expire_run)
        self.get = AsyncMock(return_value=SimpleNamespace(id=run._id))

    async def _expire_run(self) -> None:
        self.run.expired = True

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_worker_persists_failure_using_id_captured_before_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop_event = asyncio.Event()
    run = _ExpiringRun()
    session = _Session(run)
    redis = SimpleNamespace(aclose=AsyncMock())
    error = WorkRunExecutionError(
        WorkRunErrorCode.VALIDATION_FAILED,
        "generated artifact did not match the deliverable",
    )
    fail_run = AsyncMock(side_effect=lambda **_kwargs: stop_event.set())
    warning = Mock()
    exception = Mock()

    monkeypatch.setattr(worker, "AsyncSession", lambda *_args, **_kwargs: session)
    monkeypatch.setattr(worker.Redis, "from_url", Mock(return_value=redis))
    monkeypatch.setattr(worker, "claim_next_run", AsyncMock(return_value=run))
    monkeypatch.setattr(worker, "process_agentic_run", AsyncMock(side_effect=error))
    monkeypatch.setattr(worker, "fail_run", fail_run)
    monkeypatch.setattr(worker.logger, "warning", warning)
    monkeypatch.setattr(worker.logger, "exception", exception)

    await worker.run_worker(stop_event)

    session.get.assert_awaited_once_with(worker.WorkRun, run._id)
    fail_run.assert_awaited_once()
    assert fail_run.await_args.kwargs["run"].id == run._id
    assert fail_run.await_args.kwargs["error"] is error
    warning.assert_called_once()
    assert warning.call_args.kwargs["extra"]["error_code"] == (
        WorkRunErrorCode.VALIDATION_FAILED.value
    )
    exception.assert_not_called()
    redis.aclose.assert_awaited_once()


def test_unexpected_execution_failure_keeps_exception_reporting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warning = Mock()
    exception = Mock()
    error = RuntimeError("unexpected")

    monkeypatch.setattr(worker.logger, "warning", warning)
    monkeypatch.setattr(worker.logger, "exception", exception)

    try:
        raise error
    except RuntimeError as caught:
        worker._log_execution_failure(
            caught,
            run_id=uuid.uuid4(),
            executor_id="worker-1",
        )

    warning.assert_not_called()
    exception.assert_called_once()
