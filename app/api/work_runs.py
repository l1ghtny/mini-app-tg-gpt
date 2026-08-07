from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, Header, Request, status
from redis.asyncio import Redis
from sse_starlette.sse import EventSourceResponse
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.dependencies import get_bus, get_current_user, get_redis
from app.db.database import get_session
from app.db.models import AppUser
from app.redis.event_bus import RedisEventBus
from app.schemas.work_runs import (
    ArtifactDownloadResponse,
    CreateWorkRunRequest,
    WorkRunAcceptedResponse,
    WorkRunCapabilitiesResponse,
    WorkRunResponse,
)
from app.services.work_runs import service
from app.services.work_runs.contracts import WorkRunStatus


work_runs = APIRouter(tags=["work-runs"])
_TERMINAL = {
    WorkRunStatus.SUCCEEDED,
    WorkRunStatus.FAILED,
    WorkRunStatus.CANCELLED,
    WorkRunStatus.REFUNDED,
}


@work_runs.get("/work-runs/capabilities", response_model=WorkRunCapabilitiesResponse)
async def get_capabilities(
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    return await service.capabilities(session, current_user)


@work_runs.post(
    "/work-runs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=WorkRunAcceptedResponse,
)
async def create_work_run(
    payload: CreateWorkRunRequest,
    idempotency_key: str = Header(
        alias="Idempotency-Key", min_length=8, max_length=128
    ),
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
    redis: Redis = Depends(get_redis),
):
    run = await service.create_run(
        session=session,
        user=current_user,
        request=payload,
        client_request_id=idempotency_key,
    )
    await RedisEventBus(redis).publish_work(
        str(run.id),
        {
            "type": "work.queued",
            "work_run_id": str(run.id),
            "status": run.status,
            "stage": run.stage,
            "progress_percent": run.progress_percent,
        },
    )
    return WorkRunAcceptedResponse(
        id=run.id,
        status=WorkRunStatus(run.status),
        stage=run.stage,
        stream_url=f"/api/v1/work-runs/{run.id}/stream",
    )


@work_runs.post(
    "/work-runs/{run_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=WorkRunAcceptedResponse,
)
async def retry_work_run(
    run_id: uuid.UUID,
    idempotency_key: str = Header(
        alias="Idempotency-Key", min_length=8, max_length=128
    ),
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
    redis: Redis = Depends(get_redis),
):
    source = await service.owned_run(session, current_user.id, run_id)
    run = await service.retry_run(
        session=session,
        user=current_user,
        source=source,
        client_request_id=idempotency_key,
    )
    await RedisEventBus(redis).publish_work(
        str(run.id),
        {
            "type": "work.queued",
            "work_run_id": str(run.id),
            "status": run.status,
            "stage": run.stage,
            "progress_percent": run.progress_percent,
        },
    )
    return WorkRunAcceptedResponse(
        id=run.id,
        status=WorkRunStatus(run.status),
        stage=run.stage,
        stream_url=f"/api/v1/work-runs/{run.id}/stream",
    )


@work_runs.get("/work-runs/{run_id}", response_model=WorkRunResponse)
async def get_work_run(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    run = await service.owned_run(session, current_user.id, run_id)
    return await service.run_response(session, run)


@work_runs.post("/work-runs/{run_id}/cancel", response_model=WorkRunResponse)
async def cancel_work_run(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
    redis: Redis = Depends(get_redis),
):
    run = await service.owned_run(session, current_user.id, run_id)
    run = await service.cancel_run(session, run)
    await RedisEventBus(redis).publish_work(
        str(run.id),
        {
            "type": (
                "work.cancelled"
                if run.status == WorkRunStatus.CANCELLED.value
                else "work.cancelling"
            ),
            "work_run_id": str(run.id),
            "status": run.status,
            "stage": run.stage,
            "progress_percent": run.progress_percent,
        },
    )
    return await service.run_response(session, run)


@work_runs.get(
    "/work-runs/{run_id}/stream",
    response_class=EventSourceResponse,
)
async def stream_work_run(
    run_id: uuid.UUID,
    request: Request,
    last_event_id: str | None = Header(
        None, convert_underscores=False, alias="Last-Event-ID"
    ),
    bus: RedisEventBus = Depends(get_bus),
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    run = await service.owned_run(session, current_user.id, run_id)
    snapshot = await service.run_response(session, run)

    async def events():
        yield {
            "event": "snapshot",
            "data": snapshot.model_dump_json(),
        }
        if snapshot.status in _TERMINAL:
            return
        iterator = bus.read_work(str(run_id), last_event_id)
        while not await request.is_disconnected():
            event_id, fields = await anext(iterator)
            event_type = fields.get("type", "work.progress")
            yield {
                "id": event_id,
                "event": event_type,
                "data": json.dumps(fields, separators=(",", ":")),
            }
            if event_type in {"work.done", "work.error"}:
                return

    return EventSourceResponse(events())


@work_runs.get(
    "/artifacts/{artifact_id}/download",
    response_model=ArtifactDownloadResponse,
)
async def download_artifact(
    artifact_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    return await service.artifact_download(session, current_user.id, artifact_id)
