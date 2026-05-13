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

class ChatFolderCreate(ChatFolderBase):
    pass

class ChatFolderUpdate(BaseModel):
    name: Optional[str] = None
    prompt: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("prompt", "system_prompt"),
    )

class ChatFolder(ChatFolderBase):
    id: uuid.UUID
    user_id: uuid.UUID

    model_config = ConfigDict(from_attributes=True)

class ChatFolderWithConversations(ChatFolder):
    conversations: List[ConversationAPI] = []
