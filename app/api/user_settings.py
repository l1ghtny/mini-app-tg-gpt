from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.dependencies import get_current_user
from app.db.database import get_session
from app.db.models import AppUser
from app.core.config import settings
from sqlmodel import select
from app.schemas.user_settings import (UserSettingsResponse, UpdateUserSettingsRequest, OnboardingVisitRequest, OnboardingClaimRequest)
from app.services.model_registry import (
    get_text_model_provider,
    get_image_model_provider,
    get_default_image_model_for_provider,
    models_share_provider,
    TEXT_MODEL_PROVIDER,
    IMAGE_MODEL_PROVIDER,
    canonicalize_image_model,
    canonicalize_text_model,
)

user_settings = APIRouter(tags=["user/settings"], prefix="/user/settings")
_ALLOWED_DOCUMENT_PROVIDERS = {"openai", "google"}


def _onboarding_state(current_user: AppUser) -> dict:
    raw = getattr(current_user, "onboarding_state", None)
    return raw if isinstance(raw, dict) else {}


def _apply_onboarding_events(current_user: AppUser, events) -> None:
    if not events:
        return

    state = {
        key: dict(value)
        for key, value in _onboarding_state(current_user).items()
        if isinstance(value, dict)
    }
    occurred_at = datetime.now(UTC).isoformat()
    for event in events:
        item_state = dict(state.get(event.item, {}))
        if event.action in {"seen", "completed"}:
            # An explicit replay or later completion supersedes an earlier skip.
            item_state.pop("dismissed_at", None)
        item_state[f"{event.action}_at"] = occurred_at
        if event.flow_version is not None:
            item_state["flow_version"] = event.flow_version
        if event.surface is not None:
            item_state["surface"] = event.surface
        if event.choice is not None:
            item_state["choice"] = event.choice
        state[event.item] = item_state
    current_user.onboarding_state = state


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
    from app.services import allowance
    from app.services.allowance_policy import MODELS
    default_text = canonicalize_text_model(current_user.default_text_model or "gpt-5.4-nano")
    if allowance.enabled(current_user.id) and settings.SHARED_ALLOWANCE_TRIAL_ENABLED and str(current_user.id).lower() not in settings.SHARED_ALLOWANCE_PRIVATE_USER_IDS and default_text not in MODELS:
        default_text = "claude-sonnet-5"
    return UserSettingsResponse(
        language=getattr(current_user, "preferred_language", None),
        default_text_model=default_text,
        default_image_model=canonicalize_image_model(
            current_user.default_image_model or "gpt-image-1.5"
        ),
        default_document_provider=(getattr(current_user, "default_document_provider", None) or "openai"),
        default_thinking=bool(getattr(current_user, "default_thinking", True)),
        onboarding_state=_onboarding_state(current_user),
    )


@user_settings.put("", response_model=UserSettingsResponse)
async def update_user_settings(
    request: UpdateUserSettingsRequest,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    # Merge account-level onboarding events against the latest locked state.
    current_user = (await session.exec(select(AppUser).where(AppUser.id == current_user.id).with_for_update().execution_options(populate_existing=True))).one()
    text_model = canonicalize_text_model(request.default_text_model or current_user.default_text_model or "gpt-5.4-nano")
    image_model = canonicalize_image_model(
        request.default_image_model or current_user.default_image_model or "gpt-image-1.5"
    )
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

    document_provider = request.default_document_provider or getattr(current_user, "default_document_provider", None) or "openai"
    if document_provider not in _ALLOWED_DOCUMENT_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Invalid document provider: {document_provider}")

    current_user.default_text_model = text_model
    current_user.default_image_model = image_model
    current_user.default_document_provider = document_provider
    if request.language is not None:
        current_user.preferred_language = request.language
    if request.default_thinking is not None:
        current_user.default_thinking = bool(request.default_thinking)
    _apply_onboarding_events(current_user, request.onboarding_events)

    session.add(current_user)
    await session.commit()
    await session.refresh(current_user)

    return UserSettingsResponse(
        language=getattr(current_user, "preferred_language", None),
        default_text_model=current_user.default_text_model,
        default_image_model=canonicalize_image_model(current_user.default_image_model),
        default_document_provider=(getattr(current_user, "default_document_provider", None) or "openai"),
        default_thinking=bool(getattr(current_user, "default_thinking", True)),
        onboarding_state=_onboarding_state(current_user),
    )


@user_settings.post("/onboarding/visit")
async def onboarding_visit(request: OnboardingVisitRequest, session: AsyncSession = Depends(get_session), current_user: AppUser = Depends(get_current_user)):
    from app.services.onboarding import progress
    return await progress(session, current_user.id, request.session_id)


@user_settings.post("/onboarding/claim")
async def onboarding_claim(request: OnboardingClaimRequest, session: AsyncSession = Depends(get_session), current_user: AppUser = Depends(get_current_user)):
    from app.services.onboarding import claim_nudge
    return {"claimed": await claim_nudge(session, current_user.id, request.item)}
