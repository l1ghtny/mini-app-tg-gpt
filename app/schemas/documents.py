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

DocumentProvider = Literal["openai", "google"]


class DocumentProviderArtifactResponse(BaseModel):
    provider: DocumentProvider
    status: DocumentStatus
    external_file_id: Optional[str] = None
    external_index_id: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    indexed_at: Optional[datetime] = None


class DocumentUsageLocation(BaseModel):
    kind: Literal["chat", "project"]
    id: uuid.UUID
    title: str


class UserDocumentResponse(BaseModel):
    id: uuid.UUID
    filename: str
    original_filename: Optional[str] = None
    used_in: list[DocumentUsageLocation] = []
    mime_type: Optional[str] = None
    size_bytes: int
    usage_bytes: int
    status: DocumentStatus
    is_pinned: bool
    retention_state: Literal["active", "pinned", "overdue", "expired"] = "active"
    retention_enforced: bool = False
    last_used_in_search: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    primary_provider: DocumentProvider = "openai"
    provider_artifacts: list[DocumentProviderArtifactResponse] = []


class DocumentsListResponse(BaseModel):
    documents: list[UserDocumentResponse]


class DocumentSourceDownloadResponse(BaseModel):
    url: str
    expires_in: int


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
    retention_refreshes_on_search: bool = False


class ConversationDocumentsUpdateRequest(BaseModel):
    document_ids: list[uuid.UUID]
    provider_override: Optional[DocumentProvider] = None


class ConversationDocumentsUpdateResponse(BaseModel):
    conversation_id: uuid.UUID
    document_ids: list[uuid.UUID]
    effective_provider: DocumentProvider = "openai"
    tool_choice: str | list[str] | None = None
