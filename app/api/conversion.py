from fastapi import APIRouter, Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api import conversion_helpers
from app.api.dependencies import get_current_user
from app.db.database import get_session
from app.schemas.conversion import ConversionEventRequest, ConversionEventResponse, ConversionStateResponse

conversion = APIRouter(tags=["conversion"], prefix="/conversion")


@conversion.get("/state", response_model=ConversionStateResponse)
async def get_conversion_state(
    session: AsyncSession = Depends(get_session),
    user=Depends(get_current_user),
):
    return await conversion_helpers.get_conversion_state(session, user)


@conversion.post("/events", response_model=ConversionEventResponse)
async def track_conversion_event(
    request: ConversionEventRequest,
    user=Depends(get_current_user),
):
    return await conversion_helpers.track_conversion_event_for_user(user, request)
