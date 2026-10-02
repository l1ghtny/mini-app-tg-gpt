import uuid
from typing import Optional, List
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from app.schemas.chat import ConversationAPI

class ChatFolderBase(BaseModel):
    name: str
    # Accept both `prompt` (current API) and `system_prompt` (legacy UI payload).
    prompt: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("prompt", "system_prompt"),
    )
    document_ids: List[uuid.UUID] = Field(default_factory=list)

class ChatFolderCreate(ChatFolderBase):
    client_request_id: Optional[uuid.UUID] = None

class ChatFolderUpdate(BaseModel):
    name: Optional[str] = None
    prompt: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("prompt", "system_prompt"),
    )
    document_ids: Optional[List[uuid.UUID]] = None

class ChatFolder(ChatFolderBase):
    id: uuid.UUID
    user_id: uuid.UUID
    client_request_id: Optional[uuid.UUID] = None

    model_config = ConfigDict(from_attributes=True)

class ChatFolderWithConversations(ChatFolder):
    conversations: List[ConversationAPI] = []
