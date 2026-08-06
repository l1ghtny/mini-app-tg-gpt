from __future__ import annotations

import uuid
from pathlib import Path

from app.r2.client import R2_BUCKET, s3_client
from app.r2.settings import Settings


class PrivateDocumentStorageConfigurationError(RuntimeError):
    pass


def get_private_documents_bucket() -> str | None:
    bucket = Settings.R2_PRIVATE_DOCUMENTS_BUCKET.strip()
    if not bucket:
        return None
    if bucket == R2_BUCKET:
        raise PrivateDocumentStorageConfigurationError(
            "R2_PRIVATE_DOCUMENTS_BUCKET must be separate from R2_BUCKET"
        )
    if not (
        Settings.R2_PRIVATE_DOCUMENTS_ACCESS_KEY_ID
        and Settings.R2_PRIVATE_DOCUMENTS_SECRET_ACCESS_KEY
    ):
        raise PrivateDocumentStorageConfigurationError(
            "private document storage credentials are not configured"
        )
    return bucket


def build_document_source_key(
    *,
    user_id: uuid.UUID,
    document_id: uuid.UUID,
    filename: str,
) -> str:
    extension = Path(filename).suffix.lower()
    return f"documents/{user_id}/{document_id}/source{extension}"


def _require_configured_bucket(bucket: str) -> None:
    configured_bucket = get_private_documents_bucket()
    if not configured_bucket or bucket != configured_bucket:
        raise PrivateDocumentStorageConfigurationError(
            "document source bucket is not configured"
        )


def _private_s3_client():
    return s3_client(
        endpoint_url=Settings.R2_ENDPOINT,
        region_name=Settings.R2_REGION,
        access_key_id=Settings.R2_PRIVATE_DOCUMENTS_ACCESS_KEY_ID,
        secret_access_key=Settings.R2_PRIVATE_DOCUMENTS_SECRET_ACCESS_KEY,
    )


async def upload_document_source(
    *,
    bucket: str,
    key: str,
    path: str,
    content_type: str | None,
    metadata: dict[str, str],
) -> None:
    _require_configured_bucket(bucket)
    with open(path, "rb") as source_file:
        async with _private_s3_client() as s3:
            await s3.upload_fileobj(
                Fileobj=source_file,
                Bucket=bucket,
                Key=key,
                ExtraArgs={
                    "ContentType": content_type or "application/octet-stream",
                    "Metadata": metadata,
                },
            )


async def download_document_source(
    *,
    bucket: str,
    key: str,
    target_path: str,
) -> None:
    _require_configured_bucket(bucket)
    async with _private_s3_client() as s3:
        response = await s3.get_object(Bucket=bucket, Key=key)
        body = response["Body"]
        with open(target_path, "wb") as target_file:
            while chunk := await body.read(1024 * 1024):
                target_file.write(chunk)


async def delete_document_source(*, bucket: str, key: str) -> None:
    _require_configured_bucket(bucket)
    async with _private_s3_client() as s3:
        await s3.delete_object(Bucket=bucket, Key=key)
