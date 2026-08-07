from __future__ import annotations

import uuid
import os

import pytest

os.environ.setdefault("R2_BUCKET", "test-public-bucket")
os.environ.setdefault("R2_ENDPOINT", "https://example.r2.cloudflarestorage.com")
os.environ.setdefault("R2_ACCESS_KEY_ID", "test-access-key")
os.environ.setdefault("R2_SECRET_ACCESS_KEY", "test-secret-key")

from app.db.models import Artifact, ArtifactSource, WorkRun
from app.services.work_runs import service


class _Result:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def all(self) -> list[object]:
        return self.values


class _Session:
    def __init__(self, results: list[list[object]]) -> None:
        self.results = iter(results)

    async def exec(self, _statement: object) -> _Result:
        return _Result(next(self.results))


def _run(user_id: uuid.UUID, index: int) -> WorkRun:
    return WorkRun(
        user_id=user_id,
        kind="offer_comparison_xlsx",
        kind_version=2,
        status="succeeded",
        stage="completed",
        progress_percent=100,
        client_request_id=f"request-{index}",
        workflow_id=f"workflow-{index}",
    )


@pytest.mark.asyncio
async def test_work_run_history_is_paginated_and_includes_owned_artifacts() -> None:
    user_id = uuid.uuid4()
    first = _run(user_id, 1)
    second = _run(user_id, 2)
    lookahead = _run(user_id, 3)
    artifact = Artifact(
        work_run_id=first.id,
        user_id=user_id,
        kind="offer_comparison_xlsx",
        status="ready",
        filename="offer-comparison.xlsx",
        mime_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        size_bytes=7125,
    )
    source = ArtifactSource(
        artifact_id=artifact.id,
        document_id=uuid.uuid4(),
        title="offer-a.csv",
        sheet_name="CSV",
        row_start=2,
        row_end=3,
        ordinal=0,
    )
    session = _Session([[first, second, lookahead], [artifact], [source]])

    response = await service.list_run_responses(
        session,  # type: ignore[arg-type]
        user_id,
        offset=0,
        limit=2,
    )

    assert [item.id for item in response.items] == [first.id, second.id]
    assert response.items[0].artifacts[0].id == artifact.id
    assert response.items[0].artifacts[0].sources[0].title == "offer-a.csv"
    assert response.items[0].artifacts[0].sources[0].row_start == 2
    assert response.items[1].artifacts == []
    assert response.has_more is True
    assert response.offset == 0
    assert response.limit == 2


@pytest.mark.asyncio
async def test_empty_work_run_history_skips_artifact_query() -> None:
    response = await service.list_run_responses(
        _Session([[]]),  # type: ignore[arg-type]
        uuid.uuid4(),
        offset=40,
        limit=20,
    )

    assert response.items == []
    assert response.has_more is False
    assert response.offset == 40
