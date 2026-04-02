from fastapi import APIRouter, Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api import user_usage_helpers
from app.api.dependencies import get_current_user
from app.db.database import get_session
from app.schemas.usage import (
    FeatureUsageResponse,
    UserImageUsageResponse,
    UserTextUsageResponse,
)

user_usage = APIRouter(tags=["user/usage"], prefix="/user/usage")


@user_usage.get("/me/models", response_model=UserTextUsageResponse)
async def my_model_usage(
    session: AsyncSession = Depends(get_session),
    user=Depends(get_current_user),
):
    return await user_usage_helpers.get_text_usage(session, user)


@user_usage.get("/me/features", response_model=FeatureUsageResponse)
async def my_feature_usage(
    session: AsyncSession = Depends(get_session),
    user=Depends(get_current_user),
):
    return await user_usage_helpers.get_feature_usage(session, user)


@user_usage.get("/me/image-models", response_model=UserImageUsageResponse)
async def my_image_model_usage(
    session: AsyncSession = Depends(get_session),
    user=Depends(get_current_user),
):
    return await user_usage_helpers.get_image_usage(session, user)
