from pydantic import BaseModel


class ImageUploaded(BaseModel):
    key: str
    url: str