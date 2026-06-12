import uuid
from typing import List, Literal, Optional, Iterable, Union

from pydantic import BaseModel, ConfigDict

from app.services.model_registry import ImageModelName, TextModelName

AllowedModels = TextModelName
AllowedImageModels = ImageModelName
AllowedToolChoices = Literal["web_search", "file_search", "image_generation", "code_interpreter", "auto"]
PremiumSampleKind = Literal["flagship_text"]


ImageQualitySetting = Literal["low", "medium", "high"]
ImageSizeSetting = Literal["512", "1k", "2k"]


class TextContent(BaseModel):
    type: Literal["text"]
    text: str

# --- A schema for sending data to the OpenAI API ---
class ImageUrlContent(BaseModel):
    type: Literal["image_url"]
    image_url: str

# --- A schema for content when creating history for openAI API ---
class MessageContent(BaseModel):
    id: Optional[uuid.UUID] = None
    type: str
    value: str
    data: Optional[dict] = None
    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
    )


class Message(BaseModel):
    id: uuid.UUID
    role: str
    content: List[MessageContent]
    reasoning_summary: Optional[str] = None
    model_config = ConfigDict(
        from_attributes=True,
        extra="ignore",
    )


class ConversationAPI(BaseModel):
    id: uuid.UUID
    title: str
    folder_id: Optional[uuid.UUID] = None

    model_config = ConfigDict(
        from_attributes=True,
        extra="ignore",
    )


class ConversationWithMessages(ConversationAPI):
    messages: List[Message] = []


class CreateConversationRequest(BaseModel):
    folder_id: Optional[uuid.UUID] = None


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
    image_size: Optional[ImageSizeSetting] = None
    thinking: Optional[bool] = None
    reasoning_effort: Optional[str] = None
    premium_sample_kind: Optional[PremiumSampleKind] = None


class EditMessageRequest(BaseModel):
    content: str
    images: Optional[List[str]] = None


class UpdateConversationSettingsRequest(BaseModel):
    folder_id: Optional[uuid.UUID] = None
    model: Optional[AllowedModels] = None
    image_model: Optional[AllowedImageModels] = None
    tool_choice: Optional[Iterable[AllowedToolChoices]] = "auto"
    image_quality: Optional[ImageQualitySetting] = None
    image_size: Optional[ImageSizeSetting] = None
    thinking: Optional[bool] = None


class ConversationInfo(BaseModel):
    name: str
    model: AllowedModels
    image_model: AllowedImageModels
    folder_id: Optional[uuid.UUID] = None
    tool_choice: Optional[Iterable[AllowedToolChoices]] = "auto"
    image_quality: ImageQualitySetting
    image_size: ImageSizeSetting
    thinking: bool = True


class MessageCreated(BaseModel):
    user_message_id: uuid.UUID
    assistant_message_id: uuid.UUID
    message_id: uuid.UUID
    stream_url: str


class ConversationStreamRedirect(BaseModel):
    stream_url: str


class RequestExists(BaseModel):
    user_message_id: Optional[uuid.UUID] = None
    assistant_message_id: uuid.UUID
    message_id: uuid.UUID
    stream_url: Optional[str] = None
    messages_url: Optional[str] = None


class MessageUpdated(BaseModel):
    message_id: uuid.UUID
    deleted_after: int = 0
