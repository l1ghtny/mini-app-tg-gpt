from typing import Optional

from pydantic import BaseModel


class ImageUploaded(BaseModel):
    key: str
    url: str
    image_id: str | None = None
    expires_at: str | None = None
    status: str | None = None
    retention_policy: str | None = None


class ImagePrepareShareResponse(BaseModel):
    prepared_message_id: str


class ImageAssetResponse(BaseModel):
    id: str
    url: str
    status: str
    expires_at: Optional[str] = None
    retention_policy: str
    source: str
    unavailable_reason: Optional[str] = None
