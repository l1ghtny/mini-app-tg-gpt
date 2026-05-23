import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


DocumentStatus = Literal[
    "uploading",
    "processing",
    "ready",
    "failed",
    "delete_queued",
    "deleted",
]


class UserDocumentResponse(BaseModel):
    id: uuid.UUID
    filename: str
    mime_type: Optional[str] = None
    size_bytes: int
    usage_bytes: int
    status: DocumentStatus
    is_pinned: bool
    last_used_in_search: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    error_code: Optional[str] = None
    error_message: Optional[str] = None


class DocumentsListResponse(BaseModel):
    documents: list[UserDocumentResponse]


class DocumentCapabilitiesResponse(BaseModel):
    status: Literal["none", "active"]
    tier_name: Optional[str] = None
    max_active_docs: int
    active_doc_count: int
    max_pinned_docs: int
    pinned_doc_count: int
    max_storage_bytes: int
    used_storage_bytes: int
    remaining_storage_bytes: int
    max_file_size_bytes: int
    doc_retention_hours: int


class ConversationDocumentsUpdateRequest(BaseModel):
    document_ids: list[uuid.UUID]


class ConversationDocumentsUpdateResponse(BaseModel):
    conversation_id: uuid.UUID
    document_ids: list[uuid.UUID]

