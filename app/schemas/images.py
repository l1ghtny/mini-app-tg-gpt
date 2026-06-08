from pydantic import BaseModel


class ImageUploaded(BaseModel):
    key: str
    url: str


class ImagePrepareShareResponse(BaseModel):
    prepared_message_id: str