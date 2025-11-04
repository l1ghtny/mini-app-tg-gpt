import time
import uuid

from fastapi import APIRouter, UploadFile
from fastapi.params import Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.dependencies import get_current_user
from app.db.database import get_session
from app.db.models import AppUser
from app.r2.methods import upload_fileobject
from app.r2.settings import Settings
from app.schemas.images import ImageUploaded

images = APIRouter(tags=['images'], prefix='/images')

@images.post("/upload")
async def upload_image(
    image: UploadFile,
    app_user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session)
    ):
    # 0. Generate a file key
    if not image.filename:
        file_name = "image"
        image.filename = file_name

    ext = image.filename.rsplit(".", 1)[-1].lower() if "." in image.filename else "png"
    key =  f"{time.strftime('%Y/%m/%d')}/{uuid.uuid4()}.{ext}"
    # 1. Save the image to the R2 bucket
    bucket, key = await upload_fileobject(key, image, content_type=image.content_type, extra_metadata={"author": str(app_user.id), "type": "image"})
    return ImageUploaded(key=key, url=f'{Settings.R2_PUBLIC_BASE_URL}{bucket}/{key}')
