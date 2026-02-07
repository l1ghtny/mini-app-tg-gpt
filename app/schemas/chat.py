import uuid
from typing import List, Literal, Optional, Iterable, Union

from pydantic import BaseModel, ConfigDict

AllowedModels = Literal["gpt-5.2", "gpt-5-mini", "gpt-5-nano"]
AllowedImageModels = Literal["gpt-image-1.5"]
AllowedToolChoices = Literal["web_search", "file_search", "image_generation", "code_interpreter", "auto"]


ImageQualitySetting = Literal["low", "medium", "high"]


class TextContent(BaseModel):
    type: Literal["text"]
    text: str

# --- A schema for sending data to the OpenAI API ---
class ImageUrlContent(BaseModel):
    type: Literal["image_url"]
    image_url: str

# --- A schema for content when creating history for openAI API ---
class MessageContent(BaseModel):
    type: str
    value: str
    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
    )


class Message(BaseModel):
    role: str
    content: List[MessageContent]


class ConversationAPI(BaseModel):
    id: uuid.UUID
    title: str

    class ConversationAPI(BaseModel):
        model_config = ConfigDict(
            from_attributes=True,
            extra="ignore",
        )


class ConversationWithMessages(ConversationAPI):
    messages: List[Message] = []


class RenameRequest(BaseModel):
    title: str


class NewMessageRequest(BaseModel):
    client_request_id: str
    role: Literal["user", "assistant"]
    content: List[MessageContent]
    model: AllowedModels
    tool_choice: Optional[Union[AllowedToolChoices, List]] = "auto"
    image_model: Optional[AllowedImageModels] = None
    image_quality: Optional[ImageQualitySetting] = None


class UpdateConversationSettingsRequest(BaseModel):
    system_prompt: Optional[str] = None
    model: Optional[AllowedModels] = None
    image_model: Optional[AllowedImageModels] = None
    tool_choice: Optional[Iterable[AllowedToolChoices]] = "auto"
    image_quality: Optional[ImageQualitySetting]


class MessageCreated(BaseModel):
    message_id: uuid.UUID
    stream_url: str


class RequestExists(BaseModel):
    message_id: uuid.UUID
    stream_url: Optional[str]
    messages_url: Optional[str]
