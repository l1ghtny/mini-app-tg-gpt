from pydantic import BaseModel
from typing import List, Literal, Union
import uuid


class TextContent(BaseModel):
    type: Literal["text"]
    text: str

class ImageUrlContent(BaseModel):
    type: Literal["image_url"]
    image_url: str
    quality: Literal["medium"]

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


class NewMessageRequest(BaseModel):
    content: List[MessageContent]


class RenameRequest(BaseModel):
    title: str