from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from redis.asyncio import Redis
from sse_starlette.sse import EventSourceResponse
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.dependencies import get_bus, get_current_user, get_redis
from app.db.database import get_session
from app.db.models import AppUser, WorkRun
from app.db.work_agent_models import WorkThread
from app.redis.event_bus import RedisEventBus
from app.schemas.work_runs import (
    ArtifactDownloadResponse,
    ArtifactInlinePreviewResponse,
    ArtifactPreviewResponse,
    CreateWorkRunRequest,
    ReviseArtifactRequest,
    WorkRunAcceptedResponse,
    WorkRunCapabilitiesResponse,
    WorkRunListResponse,
    WorkRunResponse,
)
from app.schemas.work_threads import (
    ApproveWorkPlanRequest,
    CreateWorkFollowUpRequest,
    CreateWorkThreadRequest,
    SendWorkMessageRequest,
    UpdateWorkPlanRequest,
    WorkConversationTurnResponse,
    WorkPlanResponse,
    WorkThreadExecutionResponse,
    WorkThreadListResponse,
    WorkThreadResponse,
)
from app.services.work_runs import service
from app.services.work_runs.contracts import WorkRunStatus
from app.services.work_threads import service as thread_service


work_runs = APIRouter(tags=["work-runs"])
_TERMINAL = {
    WorkRunStatus.SUCCEEDED,
    WorkRunStatus.FAILED,
    WorkRunStatus.CANCELLED,
    WorkRunStatus.REFUNDED,
}


async def _conversation_turn_response(
    *,
    session: AsyncSession,
    redis: Redis,
    thread: WorkThread,
    run: WorkRun | None,
) -> WorkConversationTurnResponse:
    accepted = None
    if run is not None:
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
        accepted = {
            "id": run.id,
            "status": WorkRunStatus(run.status),
            "stage": run.stage,
            "stream_url": f"/api/v1/work-runs/{run.id}/stream",
        }
    return WorkConversationTurnResponse(
        thread=await thread_service.thread_response(session, thread),
        run=accepted,
    )


@work_runs.get("/work-threads", response_model=WorkThreadListResponse)
async def list_work_threads(
    offset: int = Query(default=0, ge=0, le=5000),
    limit: int = Query(default=20, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    return await thread_service.list_threads(
        session,
        current_user.id,
        offset=offset,
        limit=limit,
    )


@work_runs.post(
    "/work-threads",
    status_code=status.HTTP_201_CREATED,
    response_model=WorkThreadResponse,
)
async def create_work_thread(
    payload: CreateWorkThreadRequest,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    thread = await thread_service.create_thread(session, current_user, payload)
    return await thread_service.thread_response(session, thread)


@work_runs.post(
    "/work-conversations",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=WorkConversationTurnResponse,
)
async def create_work_conversation(
    payload: CreateWorkThreadRequest,
    idempotency_key: str = Header(
        alias="Idempotency-Key", min_length=8, max_length=128
    ),
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
    redis: Redis = Depends(get_redis),
):
    thread, run = await thread_service.start_conversation(
        session=session,
        user=current_user,
        request=payload,
        client_request_id=idempotency_key,
    )
    return await _conversation_turn_response(
        session=session,
        redis=redis,
        thread=thread,
        run=run,
    )


@work_runs.get("/work-threads/{thread_id}", response_model=WorkThreadResponse)
async def get_work_thread(
    thread_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    thread = await thread_service.owned_thread(session, current_user.id, thread_id)
    return await thread_service.thread_response(session, thread)


@work_runs.post(
    "/work-threads/{thread_id}/follow-ups",
    status_code=status.HTTP_201_CREATED,
    response_model=WorkThreadResponse,
)
async def create_work_thread_follow_up(
    thread_id: uuid.UUID,
    payload: CreateWorkFollowUpRequest,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    thread = await thread_service.owned_thread(session, current_user.id, thread_id)
    thread = await thread_service.create_follow_up(session, thread, payload)
    return await thread_service.thread_response(session, thread)


@work_runs.post(
    "/work-threads/{thread_id}/messages",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=WorkConversationTurnResponse,
)
async def send_work_thread_message(
    thread_id: uuid.UUID,
    payload: SendWorkMessageRequest,
    idempotency_key: str = Header(
        alias="Idempotency-Key", min_length=8, max_length=128
    ),
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
    redis: Redis = Depends(get_redis),
):
    thread = await thread_service.owned_thread(session, current_user.id, thread_id)
    thread, run = await thread_service.send_message(
        session=session,
        user=current_user,
        thread=thread,
        request=payload,
        client_request_id=idempotency_key,
    )
    return await _conversation_turn_response(
        session=session,
        redis=redis,
        thread=thread,
        run=run,
    )


@work_runs.post(
    "/work-threads/{thread_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=WorkConversationTurnResponse,
)
async def retry_work_thread_turn(
    thread_id: uuid.UUID,
    idempotency_key: str = Header(
        alias="Idempotency-Key", min_length=8, max_length=128
    ),
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
    redis: Redis = Depends(get_redis),
):
    thread = await thread_service.owned_thread(session, current_user.id, thread_id)
    thread, run = await thread_service.retry_conversation_turn(
        session=session,
        user=current_user,
        thread=thread,
        client_request_id=idempotency_key,
    )
    return await _conversation_turn_response(
        session=session,
        redis=redis,
        thread=thread,
        run=run,
    )


@work_runs.post(
    "/work-threads/{thread_id}/retry-plan",
    response_model=WorkThreadResponse,
)
async def retry_work_thread_plan(
    thread_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    thread = await thread_service.owned_thread(session, current_user.id, thread_id)
    thread = await thread_service.retry_plan(session, thread)
    return await thread_service.thread_response(session, thread)


@work_runs.delete(
    "/work-threads/{thread_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_failed_work_thread(
    thread_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    thread = await thread_service.owned_thread(session, current_user.id, thread_id)
    await thread_service.remove_failed_thread(session, thread)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@work_runs.put(
    "/work-threads/{thread_id}/plan",
    response_model=WorkPlanResponse,
)
async def update_work_thread_plan(
    thread_id: uuid.UUID,
    payload: UpdateWorkPlanRequest,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    thread = await thread_service.owned_thread(session, current_user.id, thread_id)
    plan = await thread_service.update_plan(session, thread, payload)
    return thread_service.plan_response(plan)


@work_runs.post(
    "/work-threads/{thread_id}/approve",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=WorkThreadExecutionResponse,
)
async def approve_work_thread_plan(
    thread_id: uuid.UUID,
    payload: ApproveWorkPlanRequest,
    idempotency_key: str = Header(
        alias="Idempotency-Key", min_length=8, max_length=128
    ),
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
    redis: Redis = Depends(get_redis),
):
    thread = await thread_service.owned_thread(session, current_user.id, thread_id)
    _, run = await thread_service.approve_plan(
        session=session,
        user=current_user,
        thread=thread,
        plan_version=payload.plan_version,
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
    return WorkThreadExecutionResponse(
        thread=await thread_service.thread_response(session, thread),
        run={
            "id": run.id,
            "status": WorkRunStatus(run.status),
            "stage": run.stage,
            "stream_url": f"/api/v1/work-runs/{run.id}/stream",
        },
    )


@work_runs.get("/work-runs/capabilities", response_model=WorkRunCapabilitiesResponse)
async def get_capabilities(
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    return await service.capabilities(session, current_user)


@work_runs.get("/work-runs", response_model=WorkRunListResponse)
async def list_work_runs(
    offset: int = Query(default=0, ge=0, le=5000),
    limit: int = Query(default=20, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    return await service.list_run_responses(
        session,
        current_user.id,
        offset=offset,
        limit=limit,
    )


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


@work_runs.get(
    "/artifacts/{artifact_id}/preview",
    response_model=ArtifactPreviewResponse,
)
async def preview_artifact(
    artifact_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    return await service.artifact_preview(session, current_user.id, artifact_id)


@work_runs.get(
    "/artifacts/{artifact_id}/inline-preview",
    response_model=ArtifactInlinePreviewResponse,
)
async def inline_preview_artifact(
    artifact_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    return await service.artifact_inline_preview(
        session,
        current_user.id,
        artifact_id,
    )


@work_runs.post(
    "/artifacts/{artifact_id}/revisions",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=WorkRunAcceptedResponse,
)
async def revise_artifact(
    artifact_id: uuid.UUID,
    payload: ReviseArtifactRequest,
    idempotency_key: str = Header(
        alias="Idempotency-Key", min_length=8, max_length=128
    ),
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
    redis: Redis = Depends(get_redis),
):
    artifact = await service.owned_artifact(session, current_user.id, artifact_id)
    run = await service.create_artifact_revision(
        session=session,
        user=current_user,
        artifact=artifact,
        revision=payload,
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
