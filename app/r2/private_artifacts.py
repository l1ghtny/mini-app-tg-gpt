from __future__ import annotations

import uuid
from pathlib import Path

from app.r2.client import s3_client
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


def _private_s3_client():
    return s3_client(
        endpoint_url=Settings.R2_ENDPOINT,
        region_name=Settings.R2_REGION,
        access_key_id=Settings.R2_PRIVATE_DOCUMENTS_ACCESS_KEY_ID,
        secret_access_key=Settings.R2_PRIVATE_DOCUMENTS_SECRET_ACCESS_KEY,
        session_token=Settings.R2_PRIVATE_DOCUMENTS_SESSION_TOKEN or None,
    )


async def upload_artifact(*, bucket: str, key: str, path: Path, sha256: str) -> None:
    if bucket != get_private_artifacts_bucket():
        raise PrivateDocumentStorageConfigurationError(
            "artifact bucket is not configured"
        )
    with path.open("rb") as artifact_file:
        async with _private_s3_client() as s3:
            await s3.put_object(
                Body=artifact_file,
                Bucket=bucket,
                Key=key,
                ContentType=(
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                ),
                Metadata={"sha256": sha256},
            )


async def delete_artifact(*, bucket: str, key: str) -> None:
    if bucket != get_private_artifacts_bucket():
        raise PrivateDocumentStorageConfigurationError(
            "artifact bucket is not configured"
        )
    async with _private_s3_client() as s3:
        await s3.delete_object(Bucket=bucket, Key=key)


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
    async with _private_s3_client() as s3:
        return await s3.generate_presigned_url(
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
