from fastapi import APIRouter, Depends, BackgroundTasks
from pydantic import BaseModel
from typing import Literal
from app.api.dependencies import get_current_user
from app.core.metrics import track_event, track_value
from app.db.models import AppUser

metrics = APIRouter(prefix="/metrics", tags=["metrics"])

class FrontendEvent(BaseModel):
    event_name: str
    tags: dict = {}
    value: float = 1.0
    type: str = "event" # "event" (counter) or "value" (distribution)

class TrackEventResponse(BaseModel):
    status: Literal["ok"]


@metrics.post("/track", response_model=TrackEventResponse)
async def track_frontend_event(
    payload: FrontendEvent,
    background_tasks: BackgroundTasks,
    user: AppUser = Depends(get_current_user)
):
    if payload.type == "value":
        background_tasks.add_task(
            track_value,
            payload.event_name,
            payload.value,
            str(user.id),
            payload.tags
        )
    else:
        background_tasks.add_task(
            track_event,
            payload.event_name,
            str(user.id),
            payload.tags
        )
    return TrackEventResponse(status="ok")
