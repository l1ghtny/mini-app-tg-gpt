from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.dependencies import get_current_user
from app.db.database import get_session
from app.db.models import AppUser
from app.schemas.user_settings import UserSettingsResponse, UpdateUserSettingsRequest
from app.services.model_registry import (
    get_text_model_provider,
    get_image_model_provider,
    get_default_image_model_for_provider,
    models_share_provider,
    TEXT_MODEL_PROVIDER,
    IMAGE_MODEL_PROVIDER,
)

user_settings = APIRouter(tags=["user/settings"], prefix="/user/settings")


def _provider_mismatch_detail(*, model: str, image_model: str) -> dict[str, str]:
    return {
        "error": "provider_mismatch",
        "message": "Text and image models must use the same provider.",
        "model": model,
        "model_provider": get_text_model_provider(model),
        "image_model": image_model,
        "image_model_provider": get_image_model_provider(image_model),
    }


@user_settings.get("", response_model=UserSettingsResponse)
async def get_user_settings(
    current_user: AppUser = Depends(get_current_user),
):
    return UserSettingsResponse(
        default_text_model=current_user.default_text_model or "gpt-5.4-nano",
        default_image_model=current_user.default_image_model or "gpt-image-1.5",
        default_thinking=bool(getattr(current_user, "default_thinking", True)),
    )


@user_settings.put("", response_model=UserSettingsResponse)
async def update_user_settings(
    request: UpdateUserSettingsRequest,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    text_model = request.default_text_model or current_user.default_text_model or "gpt-5.4-nano"
    image_model = request.default_image_model or current_user.default_image_model or "gpt-image-1.5"
    explicit_image_model = request.default_image_model is not None

    if text_model not in TEXT_MODEL_PROVIDER:
        raise HTTPException(status_code=400, detail=f"Invalid text model: {text_model}")
    if image_model not in IMAGE_MODEL_PROVIDER:
        raise HTTPException(status_code=400, detail=f"Invalid image model: {image_model}")

    if explicit_image_model and not models_share_provider(text_model, image_model):
        raise HTTPException(
            status_code=400,
            detail=_provider_mismatch_detail(model=text_model, image_model=image_model),
        )

    if not explicit_image_model and not models_share_provider(text_model, image_model):
        image_model = get_default_image_model_for_provider(get_text_model_provider(text_model))

    current_user.default_text_model = text_model
    current_user.default_image_model = image_model
    if request.default_thinking is not None:
        current_user.default_thinking = bool(request.default_thinking)

    session.add(current_user)
    await session.commit()
    await session.refresh(current_user)

    return UserSettingsResponse(
        default_text_model=current_user.default_text_model,
        default_image_model=current_user.default_image_model,
        default_thinking=bool(getattr(current_user, "default_thinking", True)),
    )
