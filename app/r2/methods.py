import io
import time
from typing import Optional
from fastapi import UploadFile


from app.r2.client import s3_client, R2_BUCKET

# (A) Put raw bytes (good for small generated images)
async def put_bytes(key: str, data: bytes, content_type: str = "application/octet-stream", metadata: Optional[dict] = None):
    async with s3_client() as s3:
        await s3.put_object(
            Bucket=R2_BUCKET,
            Key=key,
            Body=io.BytesIO(data),
            ContentType=content_type,
            Metadata=metadata or {},
        )
    return R2_BUCKET, key

# (B) Stream upload from UploadFile (doesn’t load a whole file in memory)
async def upload_fileobject(key: str, file: UploadFile, content_type: Optional[str] = None, extra_metadata: Optional[dict] = None):
    # Prefer upload_fileobj: internally does multipart when needed
    async with s3_client() as s3:
        await s3.upload_fileobj(
            Fileobj=file.file,  # SpooledTemporaryFile; already a file-like stream
            Bucket=R2_BUCKET,
            Key=key,
            ExtraArgs={
                "ContentType": content_type or (file.content_type or "application/octet-stream"),
                "Metadata": extra_metadata or {},
            },
        )
    return R2_BUCKET, key

# (C) Presigned GET (private objects)
async def presign_get(key: str, expires: int = 900) -> str:
    async with s3_client() as s3:
        url = await s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": R2_BUCKET, "Key": key},
            ExpiresIn=expires,
        )
    return url

# (D) Presigned POST (direct browser upload)
async def presign_post(key: str, content_type: str, max_mb: int = 30, expires: int = 900):
    async with s3_client() as s3:
        conditions = [
            ["content-length-range", 0, max_mb * 1024 * 1024],
            {"Content-Type": content_type},
        ]
        # generate_presigned_post is available on the client too
        post = await s3.generate_presigned_post(
            Bucket=R2_BUCKET,
            Key=key,
            Fields={"Content-Type": content_type},
            Conditions=conditions,
            ExpiresIn=expires,
        )
    return {
        "object_key": key,
        "url": post["url"],
        "fields": post["fields"],
        "expire_at": int(time.time()) + expires,
    }

# (E) Head object (metadata fetch)
async def head_object(key: str):
    async with s3_client() as s3:
        return await s3.head_object(Bucket=R2_BUCKET, Key=key)

# (F) Delete
async def delete_object(key: str):
    async with s3_client() as s3:
        await s3.delete_object(Bucket=R2_BUCKET, Key=key)