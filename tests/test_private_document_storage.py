import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("R2_BUCKET", "test-public-bucket")
os.environ.setdefault("R2_ENDPOINT", "https://example.r2.cloudflarestorage.com")
os.environ.setdefault("R2_ACCESS_KEY_ID", "test-access-key")
os.environ.setdefault("R2_SECRET_ACCESS_KEY", "test-secret-key")

from app.r2 import private_documents
from app.r2.private_documents import (
    PrivateDocumentStorageConfigurationError,
    build_document_source_key,
    delete_document_source,
    download_document_source,
    get_private_documents_bucket,
    upload_document_source,
)
from app.r2.settings import Settings


def test_private_document_bucket_is_disabled_when_unconfigured(monkeypatch) -> None:
    monkeypatch.setattr(Settings, "R2_PRIVATE_DOCUMENTS_BUCKET", "")

    assert get_private_documents_bucket() is None


def test_private_document_bucket_must_not_reuse_public_bucket(monkeypatch) -> None:
    monkeypatch.setattr(Settings, "R2_PRIVATE_DOCUMENTS_BUCKET", "public-images")
    monkeypatch.setattr(private_documents, "R2_BUCKET", "public-images")

    with pytest.raises(PrivateDocumentStorageConfigurationError):
        get_private_documents_bucket()


def test_private_document_bucket_requires_separate_credentials(monkeypatch) -> None:
    monkeypatch.setattr(Settings, "R2_PRIVATE_DOCUMENTS_BUCKET", "private-documents")
    monkeypatch.setattr(Settings, "R2_PRIVATE_DOCUMENTS_ACCESS_KEY_ID", "")
    monkeypatch.setattr(Settings, "R2_PRIVATE_DOCUMENTS_SECRET_ACCESS_KEY", "")

    with pytest.raises(PrivateDocumentStorageConfigurationError):
        get_private_documents_bucket()


def test_private_document_bucket_rejects_expired_temporary_credentials(
    monkeypatch,
) -> None:
    monkeypatch.setattr(Settings, "R2_PRIVATE_DOCUMENTS_BUCKET", "private-documents")
    monkeypatch.setattr(
        Settings, "R2_PRIVATE_DOCUMENTS_ACCESS_KEY_ID", "temporary-access-key"
    )
    monkeypatch.setattr(
        Settings, "R2_PRIVATE_DOCUMENTS_SECRET_ACCESS_KEY", "temporary-secret-key"
    )
    monkeypatch.setattr(
        Settings,
        "R2_PRIVATE_DOCUMENTS_CREDENTIAL_EXPIRES_AT",
        (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    )

    with pytest.raises(PrivateDocumentStorageConfigurationError):
        get_private_documents_bucket()


def test_document_source_key_is_deterministic_and_hides_filename() -> None:
    user_id = uuid.uuid4()
    document_id = uuid.uuid4()

    key = build_document_source_key(
        user_id=user_id,
        document_id=document_id,
        filename="Customer Price List.xlsx",
    )

    assert key == f"documents/{user_id}/{document_id}/source.xlsx"
    assert "Customer" not in key


class _MemoryBody:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0

    async def read(self, size: int) -> bytes:
        chunk = self._data[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


class _MemoryS3:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.extra_args: dict[str, object] | None = None

    async def upload_fileobj(self, *, Fileobj, Bucket, Key, ExtraArgs) -> None:
        self.objects[(Bucket, Key)] = Fileobj.read()
        self.extra_args = ExtraArgs

    async def get_object(self, *, Bucket, Key):
        return {"Body": _MemoryBody(self.objects[(Bucket, Key)])}

    async def delete_object(self, *, Bucket, Key) -> None:
        self.objects.pop((Bucket, Key), None)


@pytest.mark.asyncio
async def test_private_document_source_round_trip(monkeypatch, tmp_path: Path) -> None:
    bucket = "private-documents"
    client = _MemoryS3()

    @asynccontextmanager
    async def fake_s3_client(**kwargs):
        assert kwargs == {
            "endpoint_url": Settings.R2_ENDPOINT,
            "region_name": Settings.R2_REGION,
            "access_key_id": "private-access-key",
            "secret_access_key": "private-secret-key",
            "session_token": "temporary-session-token",
        }
        yield client

    monkeypatch.setattr(Settings, "R2_PRIVATE_DOCUMENTS_BUCKET", bucket)
    monkeypatch.setattr(
        Settings,
        "R2_PRIVATE_DOCUMENTS_ACCESS_KEY_ID",
        "private-access-key",
    )
    monkeypatch.setattr(
        Settings,
        "R2_PRIVATE_DOCUMENTS_SECRET_ACCESS_KEY",
        "private-secret-key",
    )
    monkeypatch.setattr(
        Settings,
        "R2_PRIVATE_DOCUMENTS_SESSION_TOKEN",
        "temporary-session-token",
    )
    monkeypatch.setattr(
        Settings,
        "R2_PRIVATE_DOCUMENTS_CREDENTIAL_EXPIRES_AT",
        "",
    )
    monkeypatch.setattr(private_documents, "s3_client", fake_s3_client)

    source = tmp_path / "source.csv"
    source.write_bytes(b"name,price\nTea,100\n")
    target = tmp_path / "downloaded.csv"
    key = "documents/user/document/source.csv"

    await upload_document_source(
        bucket=bucket,
        key=key,
        path=str(source),
        content_type="text/csv",
        metadata={"document-id": "document"},
    )
    await download_document_source(bucket=bucket, key=key, target_path=str(target))

    assert target.read_bytes() == source.read_bytes()
    assert client.extra_args == {
        "ContentType": "text/csv",
        "Metadata": {"document-id": "document"},
    }

    await delete_document_source(bucket=bucket, key=key)

    assert client.objects == {}


@pytest.mark.asyncio
async def test_private_document_source_rejects_unconfigured_bucket(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(Settings, "R2_PRIVATE_DOCUMENTS_BUCKET", "expected")
    monkeypatch.setattr(
        Settings,
        "R2_PRIVATE_DOCUMENTS_ACCESS_KEY_ID",
        "private-access-key",
    )
    monkeypatch.setattr(
        Settings,
        "R2_PRIVATE_DOCUMENTS_SECRET_ACCESS_KEY",
        "private-secret-key",
    )
    source = tmp_path / "source.csv"
    source.write_text("name,price\n", encoding="utf-8")

    with pytest.raises(PrivateDocumentStorageConfigurationError):
        await upload_document_source(
            bucket="unexpected",
            key="source.csv",
            path=str(source),
            content_type="text/csv",
            metadata={},
        )
