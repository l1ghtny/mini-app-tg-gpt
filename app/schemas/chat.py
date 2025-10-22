import uuid
from typing import List, Literal, Optional


from pydantic import BaseModel


AllowedModels = Literal["gpt-5", "gpt-5-mini", "gpt-5-nano"]
AllowedToolChoices = Literal["web_search", "file_search", "image_generation", "code_interpreter"]


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


class Conversation(BaseModel):
    id: uuid.UUID
    title: str

    class Config:
        from_attributes = True


class ConversationWithMessages(Conversation):
    messages: List[Message] = []


class RenameRequest(BaseModel):
    title: str


class NewMessageRequest(BaseModel):
    content: List[MessageContent]
    system_prompt: Optional[str] = None
    model: AllowedModels
    tool_choice: Optional[AllowedToolChoices]


class UpdateConversationSettingsRequest(BaseModel):
    system_prompt: Optional[str] = None
    model: Optional[AllowedModels] = None
