from __future__ import annotations

import asyncio
import uuid
from functools import lru_cache
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from app.r2.client import _client_kwargs
from app.r2.private_documents import (
    PrivateDocumentStorageConfigurationError,
    get_private_documents_bucket,
)
from app.r2.settings import Settings


def get_private_artifacts_bucket() -> str:
    bucket = get_private_documents_bucket()
    if not bucket:
        raise PrivateDocumentStorageConfigurationError(
            "private artifact storage is not configured"
        )
    return bucket


def build_artifact_key(
    *, user_id: uuid.UUID, work_run_id: uuid.UUID, artifact_id: uuid.UUID
) -> str:
    return f"artifacts/{user_id}/{work_run_id}/{artifact_id}/comparison-v1.xlsx"


@lru_cache(maxsize=1)
def _private_s3_client():
    return boto3.client(
        **_client_kwargs(
            endpoint_url=Settings.R2_ENDPOINT,
            region_name=Settings.R2_REGION,
            access_key_id=Settings.R2_PRIVATE_DOCUMENTS_ACCESS_KEY_ID,
            secret_access_key=Settings.R2_PRIVATE_DOCUMENTS_SECRET_ACCESS_KEY,
            session_token=Settings.R2_PRIVATE_DOCUMENTS_SESSION_TOKEN or None,
        )
    )


def _put_artifact(*, bucket: str, key: str, path: Path, sha256: str) -> None:
    client = _private_s3_client()
    with path.open("rb") as artifact_file:
        client.put_object(
            Body=artifact_file,
            Bucket=bucket,
            Key=key,
            ContentType=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            Metadata={"sha256": sha256},
        )


async def upload_artifact(*, bucket: str, key: str, path: Path, sha256: str) -> None:
    if bucket != get_private_artifacts_bucket():
        raise PrivateDocumentStorageConfigurationError(
            "artifact bucket is not configured"
        )
    await asyncio.to_thread(
        _put_artifact,
        bucket=bucket,
        key=key,
        path=path,
        sha256=sha256,
    )


def _artifact_object_matches(
    *,
    bucket: str,
    key: str,
    size_bytes: int,
    sha256: str,
) -> bool:
    client = _private_s3_client()
    try:
        response = client.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        error_code = str(exc.response.get("Error", {}).get("Code", ""))
        if error_code in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise
    return (
        response.get("ContentLength") == size_bytes
        and response.get("Metadata", {}).get("sha256") == sha256
    )


async def artifact_object_matches(
    *,
    bucket: str,
    key: str,
    size_bytes: int,
    sha256: str,
) -> bool:
    if bucket != get_private_artifacts_bucket():
        raise PrivateDocumentStorageConfigurationError(
            "artifact bucket is not configured"
        )
    return await asyncio.to_thread(
        _artifact_object_matches,
        bucket=bucket,
        key=key,
        size_bytes=size_bytes,
        sha256=sha256,
    )


async def delete_artifact(*, bucket: str, key: str) -> None:
    if bucket != get_private_artifacts_bucket():
        raise PrivateDocumentStorageConfigurationError(
            "artifact bucket is not configured"
        )

    def delete() -> None:
        _private_s3_client().delete_object(Bucket=bucket, Key=key)

    await asyncio.to_thread(delete)


async def presign_artifact_download(
    *, bucket: str, key: str, filename: str, expires: int = 900
) -> str:
    if bucket != get_private_artifacts_bucket():
        raise PrivateDocumentStorageConfigurationError(
            "artifact bucket is not configured"
        )
    safe_filename = (
        "".join(
            character
            for character in Path(filename).name
            if character.isascii() and (character.isalnum() or character in "._-")
        )
        or "comparison.xlsx"
    )

    def presign() -> str:
        return _private_s3_client().generate_presigned_url(
            "get_object",
            Params={
                "Bucket": bucket,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{safe_filename}"',
                "ResponseContentType": (
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                ),
            },
            ExpiresIn=expires,
        )

    return await asyncio.to_thread(presign)
