import uuid
from typing import List, Literal, Optional


from pydantic import BaseModel


AllowedModels = Literal["gpt-5", "gpt-5-mini", "gpt-5-nano"]
AllowedToolChoices = Literal["web_search", "file_search", "image_generation", "code_interpreter", "auto"]


class TextContent(BaseModel):
    type: Literal["text"]
    text: str

class ImageUrlContent(BaseModel):
    type: Literal["image_url"]
    image_url: str
    quality: Optional[Literal["medium"]]

# --- A schema for content when reading from the DB ---
class MessageContent(BaseModel):
    type: str
    value: str
    class Config:
        from_attributes = True


class Message(BaseModel):
    role: str
    content: List[MessageContent]


class ConversationAPI(BaseModel):
    id: uuid.UUID
    title: str

    class Config:
        from_attributes = True


class ConversationWithMessages(ConversationAPI):
    messages: List[Message] = []


class RenameRequest(BaseModel):
    title: str


class NewMessageRequest(BaseModel):
    content: List[MessageContent]
    model: AllowedModels
    tool_choice: Optional[AllowedToolChoices] = None


class UpdateConversationSettingsRequest(BaseModel):
    system_prompt: Optional[str] = None
    model: Optional[AllowedModels] = None
    tool_choice: Optional[AllowedToolChoices] = None


class MessageCreated(BaseModel):
    message_id: uuid.UUID
    stream_url: str
