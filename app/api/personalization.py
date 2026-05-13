from fastapi import APIRouter, Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api import personalization_helpers
from app.api.dependencies import get_current_user
from app.db.database import get_session
from app.db.models import AppUser
from app.schemas.personalization import (
    PersonalizationComposeRequest,
    PersonalizationComposeResponse,
    PersonalizationDismissResponse,
    PersonalizationProfileResponse,
    PersonalizationSkipResponse,
    PersonalizationWizardResponse,
    UpdatePersonalizationRequest,
)

personalization = APIRouter(tags=["user/personalization"], prefix="/user/personalization")


@personalization.get("", response_model=PersonalizationProfileResponse)
async def get_personalization_profile(
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    return await personalization_helpers.get_personalization_profile(session, current_user)


@personalization.patch("", response_model=PersonalizationProfileResponse)
async def update_personalization_profile(
    request: UpdatePersonalizationRequest,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    return await personalization_helpers.update_personalization_profile(session, current_user, request)


@personalization.post("/dismiss", response_model=PersonalizationDismissResponse)
async def dismiss_personalization_prompt(
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    return await personalization_helpers.dismiss_personalization_prompt(session, current_user)


@personalization.post("/skip", response_model=PersonalizationSkipResponse)
async def skip_personalization_prompt(
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    return await personalization_helpers.skip_personalization_prompt(session, current_user)


@personalization.get("/wizard", response_model=PersonalizationWizardResponse)
async def get_personalization_wizard():
    return await personalization_helpers.get_personalization_wizard()


@personalization.post("/wizard/compose", response_model=PersonalizationComposeResponse)
async def compose_personalization_prompt(request: PersonalizationComposeRequest):
    return await personalization_helpers.compose_personalization_prompt(request)
