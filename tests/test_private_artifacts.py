from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("R2_BUCKET", "test-public-bucket")
os.environ.setdefault("R2_ENDPOINT", "https://example.r2.cloudflarestorage.com")
os.environ.setdefault("R2_ACCESS_KEY_ID", "test-access-key")
os.environ.setdefault("R2_SECRET_ACCESS_KEY", "test-secret-key")

from app.r2 import private_artifacts
from app.services.work_runs import artifact_storage_process
from app.services.work_runs import service as work_run_service
from app.services.work_runs.service import _artifact_storage_identity


class _FakeS3Client:
    def __init__(self, head_response: dict[str, object] | None = None) -> None:
        self.put_calls: list[dict[str, object]] = []
        self.head_response = head_response

    def put_object(self, **kwargs: object) -> None:
        body = kwargs["Body"]
        self.put_calls.append({**kwargs, "Body": body.read()})

    def head_object(self, **_: object) -> dict[str, object]:
        assert self.head_response is not None
        return self.head_response


def test_artifact_key_keeps_revision_versions_distinct() -> None:
    user_id = uuid.uuid4()
    run_id = uuid.uuid4()
    artifact_id = uuid.uuid4()

    key = private_artifacts.build_artifact_key(
        user_id=user_id,
        work_run_id=run_id,
        artifact_id=artifact_id,
        version=3,
    )

    assert key.endswith("/comparison-v3.xlsx")


@pytest.mark.asyncio
async def test_upload_artifact_uses_single_put_object(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    artifact_path = tmp_path / "comparison.xlsx"
    artifact_path.write_bytes(b"workbook")
    client = _FakeS3Client()

    monkeypatch.setattr(
        private_artifacts,
        "get_private_artifacts_bucket",
        lambda: "private-documents",
    )
    monkeypatch.setattr(private_artifacts, "_private_s3_client", lambda: client)

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

    monkeypatch.setattr(
        private_artifacts,
        "get_private_artifacts_bucket",
        lambda: "private-documents",
    )
    monkeypatch.setattr(private_artifacts, "_private_s3_client", lambda: client)

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


def test_storage_process_reuses_matching_persisted_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        artifact_storage_process,
        "get_private_artifacts_bucket",
        lambda: "private-documents",
    )
    monkeypatch.setattr(
        artifact_storage_process,
        "_artifact_object_matches",
        lambda **_: True,
    )
    put_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        artifact_storage_process,
        "_put_artifact",
        lambda **kwargs: put_calls.append(kwargs),
    )

    uploaded = artifact_storage_process.reconcile_artifact_storage(
        bucket="private-documents",
        key="artifacts/run/comparison-v1.xlsx",
        path=tmp_path / "comparison.xlsx",
        rendered_size_bytes=9,
        rendered_sha256="b" * 64,
        stored_size_bytes=8,
        stored_sha256="a" * 64,
    )

    assert uploaded is False
    assert put_calls == []


def test_storage_process_uploads_and_verifies_missing_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        artifact_storage_process,
        "get_private_artifacts_bucket",
        lambda: "private-documents",
    )
    match_results = iter((False, True))
    monkeypatch.setattr(
        artifact_storage_process,
        "_artifact_object_matches",
        lambda **_: next(match_results),
    )
    put_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        artifact_storage_process,
        "_put_artifact",
        lambda **kwargs: put_calls.append(kwargs),
    )
    artifact_path = tmp_path / "comparison.xlsx"

    uploaded = artifact_storage_process.reconcile_artifact_storage(
        bucket="private-documents",
        key="artifacts/run/comparison-v1.xlsx",
        path=artifact_path,
        rendered_size_bytes=9,
        rendered_sha256="b" * 64,
        stored_size_bytes=8,
        stored_sha256="a" * 64,
    )

    assert uploaded is True
    assert put_calls == [
        {
            "bucket": "private-documents",
            "key": "artifacts/run/comparison-v1.xlsx",
            "path": artifact_path,
            "sha256": "b" * 64,
        }
    ]


@pytest.mark.asyncio
async def test_storage_subprocess_result_is_parsed_without_async_child_watcher(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    commands: list[list[str]] = []

    def run_process(command: list[str]) -> subprocess.CompletedProcess[bytes]:
        commands.append(command)
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=b'{"uploaded":false}',
            stderr=b"",
        )

    monkeypatch.setattr(
        work_run_service,
        "_run_artifact_storage_process",
        run_process,
    )

    uploaded = await work_run_service._store_artifact_in_subprocess(
        bucket="private-documents",
        key="artifacts/run/comparison-v1.xlsx",
        output_path=tmp_path / "comparison.xlsx",
        rendered_size_bytes=9,
        rendered_sha256="b" * 64,
        stored_size_bytes=8,
        stored_sha256="a" * 64,
    )

    assert uploaded is False
    assert commands[0][0:3] == [
        work_run_service.sys.executable,
        "-m",
        "app.services.work_runs.artifact_storage_process",
    ]


@pytest.mark.asyncio
async def test_progress_publish_timeout_does_not_block_durable_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publish_started = asyncio.Event()

    class HangingEventBus:
        def __init__(self, _: object) -> None:
            pass

        async def publish_work(self, *_: object) -> str:
            publish_started.set()
            await asyncio.Event().wait()
            return "unused"

    monkeypatch.setattr(work_run_service, "RedisEventBus", HangingEventBus)
    run = SimpleNamespace(
        id="run-id",
        status="storing",
        stage="storing_artifact",
        progress_percent=90,
    )

    await work_run_service._publish(object(), run, "work.stage")
    await asyncio.wait_for(publish_started.wait(), timeout=0.1)

    assert publish_started.is_set()
