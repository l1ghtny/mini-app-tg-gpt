from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("R2_BUCKET", "test-public-bucket")
os.environ.setdefault("R2_ENDPOINT", "https://example.r2.cloudflarestorage.com")
os.environ.setdefault("R2_ACCESS_KEY_ID", "test-access-key")
os.environ.setdefault("R2_SECRET_ACCESS_KEY", "test-secret-key")

from app.r2 import private_artifacts
from app.services.work_runs import service as work_run_service
from app.services.work_runs.service import _artifact_storage_identity


class _FakeS3Client:
    def __init__(self, head_response: dict[str, object] | None = None) -> None:
        self.put_calls: list[dict[str, object]] = []
        self.head_response = head_response

    async def put_object(self, **kwargs: object) -> None:
        body = kwargs["Body"]
        self.put_calls.append({**kwargs, "Body": body.read()})

    async def head_object(self, **_: object) -> dict[str, object]:
        assert self.head_response is not None
        return self.head_response


@pytest.mark.asyncio
async def test_upload_artifact_uses_single_put_object(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    artifact_path = tmp_path / "comparison.xlsx"
    artifact_path.write_bytes(b"workbook")
    client = _FakeS3Client()

    @asynccontextmanager
    async def fake_client():
        yield client

    monkeypatch.setattr(
        private_artifacts,
        "get_private_artifacts_bucket",
        lambda: "private-documents",
    )
    monkeypatch.setattr(private_artifacts, "_private_s3_client", fake_client)

    await private_artifacts.upload_artifact(
        bucket="private-documents",
        key="artifacts/run/comparison-v1.xlsx",
        path=artifact_path,
        sha256="a" * 64,
    )

    assert client.put_calls == [
        {
            "Body": b"workbook",
            "Bucket": "private-documents",
            "Key": "artifacts/run/comparison-v1.xlsx",
            "ContentType": (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            "Metadata": {"sha256": "a" * 64},
        }
    ]


@pytest.mark.asyncio
async def test_artifact_object_matches_size_and_checksum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeS3Client(
        head_response={
            "ContentLength": 8,
            "Metadata": {"sha256": "a" * 64},
        }
    )

    @asynccontextmanager
    async def fake_client():
        yield client

    monkeypatch.setattr(
        private_artifacts,
        "get_private_artifacts_bucket",
        lambda: "private-documents",
    )
    monkeypatch.setattr(private_artifacts, "_private_s3_client", fake_client)

    assert await private_artifacts.artifact_object_matches(
        bucket="private-documents",
        key="artifacts/run/comparison-v1.xlsx",
        size_bytes=8,
        sha256="a" * 64,
    )
    assert not await private_artifacts.artifact_object_matches(
        bucket="private-documents",
        key="artifacts/run/comparison-v1.xlsx",
        size_bytes=9,
        sha256="a" * 64,
    )


def test_recovery_uses_the_persisted_artifact_identity() -> None:
    artifact = SimpleNamespace(size_bytes=8, sha256="a" * 64)

    assert _artifact_storage_identity(
        artifact,
        rendered_size_bytes=9,
        rendered_sha256="b" * 64,
    ) == (8, "a" * 64)


@pytest.mark.asyncio
async def test_timed_out_upload_reconciles_without_waiting_for_task_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    upload_started = asyncio.Event()

    async def hanging_upload(**_: object) -> None:
        upload_started.set()
        await asyncio.Event().wait()

    async def matching_object(**_: object) -> bool:
        return True

    monkeypatch.setattr(work_run_service, "upload_artifact", hanging_upload)
    monkeypatch.setattr(
        work_run_service,
        "artifact_object_matches",
        matching_object,
    )
    monkeypatch.setattr(work_run_service, "_ARTIFACT_UPLOAD_TIMEOUT_SECONDS", 0.01)

    await work_run_service._upload_artifact_with_reconciliation(
        bucket="private-documents",
        key="artifacts/run/comparison-v1.xlsx",
        output_path=tmp_path / "comparison.xlsx",
        size_bytes=8,
        sha256="a" * 64,
    )

    assert upload_started.is_set()
