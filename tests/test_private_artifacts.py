from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from app.r2 import private_artifacts


class _FakeS3Client:
    def __init__(self) -> None:
        self.put_calls: list[dict[str, object]] = []

    async def put_object(self, **kwargs: object) -> None:
        body = kwargs["Body"]
        self.put_calls.append({**kwargs, "Body": body.read()})


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
