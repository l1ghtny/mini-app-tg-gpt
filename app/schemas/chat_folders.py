import uuid
from typing import Optional, List
from pydantic import BaseModel, ConfigDict
from app.schemas.chat import ConversationAPI

class ChatFolderBase(BaseModel):
    name: str
    prompt: Optional[str] = None

class ChatFolderCreate(ChatFolderBase):
    pass

class ChatFolderUpdate(BaseModel):
    name: Optional[str] = None
    prompt: Optional[str] = None

class ChatFolder(ChatFolderBase):
    id: uuid.UUID
    user_id: uuid.UUID

    model_config = ConfigDict(from_attributes=True)

class ChatFolderWithConversations(ChatFolder):
    conversations: List[ConversationAPI] = []
