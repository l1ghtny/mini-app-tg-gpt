from __future__ import annotations

import json
import uuid
from datetime import timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import delete
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import (
    AppUser,
    ChatFolder,
    Conversation,
    UserDocument,
    WorkRun,
    WorkRunPolicy,
    utcnow_naive,
)
from app.db.work_agent_models import WorkPlan, WorkThread, WorkThreadMessage, WorkThreadRun
from app.schemas.work_runs import CreateWorkRunRequest, OfferComparisonOptions
from app.schemas.work_threads import (
    CreateWorkFollowUpRequest,
    CreateWorkThreadRequest,
    SendWorkMessageRequest,
    UpdateWorkPlanRequest,
    WorkPlanResponse,
    WorkThreadListResponse,
    WorkThreadMessageResponse,
    WorkThreadResponse,
    WorkThreadSummaryResponse,
)
from app.services.work_runs import service as run_service
from app.services.work_runs.contracts import WorkRunKind, WorkRunStatus
from app.services.work_runs.normalization import NormalizationUsage
from app.services.work_threads.planner import PlannerResult, WorkPlanningError, plan_work


async def _owned_documents(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    document_ids: list[uuid.UUID],
) -> list[UserDocument]:
    if not document_ids:
        return []
    documents = (
        await session.exec(
            select(UserDocument).where(
                UserDocument.user_id == user_id,
                col(UserDocument.id).in_(document_ids),
                UserDocument.deleted_at.is_(None),
            )
        )
    ).all()
    if len(documents) != len(document_ids):
        raise HTTPException(status_code=422, detail="work_thread_invalid_documents")
    by_id = {document.id: document for document in documents}
    ordered = [by_id[document_id] for document_id in document_ids]
    if any(document.status != "ready" for document in ordered):
        raise HTTPException(status_code=409, detail="work_thread_documents_not_ready")
    return ordered


async def _validate_context(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    folder_id: uuid.UUID | None,
) -> None:
    if conversation_id:
        conversation = (
            await session.exec(
                select(Conversation).where(
                    Conversation.id == conversation_id,
                    Conversation.user_id == user_id,
                )
            )
        ).first()
        if conversation is None:
            raise HTTPException(status_code=422, detail="work_thread_invalid_conversation")
    if folder_id:
        folder = (
            await session.exec(
                select(ChatFolder).where(
                    ChatFolder.id == folder_id,
                    ChatFolder.user_id == user_id,
                )
            )
        ).first()
        if folder is None:
            raise HTTPException(status_code=422, detail="work_thread_invalid_folder")


def _message_response(message: WorkThreadMessage) -> WorkThreadMessageResponse:
    return WorkThreadMessageResponse(
        id=message.id,
        role=message.role,
        kind=message.kind,
        content=message.content,
        metadata=message.message_metadata,
        created_at=message.created_at.replace(tzinfo=timezone.utc),
    )


def plan_response(plan: WorkPlan) -> WorkPlanResponse:
    return WorkPlanResponse(
        id=plan.id,
        version=plan.version,
        status=plan.status,
        title=plan.title,
        summary=plan.summary,
        execution_kind=plan.execution_kind,
        steps=plan.steps,
        expected_outputs=plan.expected_outputs,
        assumptions=plan.assumptions,
        created_at=plan.created_at.replace(tzinfo=timezone.utc),
        approved_at=(
            plan.approved_at.replace(tzinfo=timezone.utc) if plan.approved_at else None
        ),
    )


async def _latest_plan(session: AsyncSession, thread_id: uuid.UUID) -> WorkPlan | None:
    return (
        await session.exec(
            select(WorkPlan)
            .where(WorkPlan.thread_id == thread_id)
            .order_by(col(WorkPlan.version).desc())
            .limit(1)
        )
    ).first()


async def owned_thread(
    session: AsyncSession,
    user_id: uuid.UUID,
    thread_id: uuid.UUID,
) -> WorkThread:
    thread = (
        await session.exec(
            select(WorkThread).where(
                WorkThread.id == thread_id,
                WorkThread.user_id == user_id,
            )
        )
    ).first()
    if thread is None:
        raise HTTPException(status_code=404, detail="work_thread_not_found")
    return thread


async def thread_response(
    session: AsyncSession,
    thread: WorkThread,
) -> WorkThreadResponse:
    messages = (
        await session.exec(
            select(WorkThreadMessage)
            .where(WorkThreadMessage.thread_id == thread.id)
            .order_by(WorkThreadMessage.created_at)
        )
    ).all()
    plan = await _latest_plan(session, thread.id)
    links = (
        await session.exec(
            select(WorkThreadRun)
            .where(WorkThreadRun.thread_id == thread.id)
            .order_by(WorkThreadRun.ordinal)
        )
    ).all()
    runs = []
    for link in links:
        run = await session.get(WorkRun, link.work_run_id)
        if run is not None:
            runs.append(await run_service.run_response(session, run))
    document_ids = [
        uuid.UUID(value) for value in thread.context_manifest.get("document_ids", [])
    ]
    return WorkThreadResponse(
        id=thread.id,
        title=thread.title,
        goal=thread.goal,
        status=thread.status,
        conversation_id=thread.conversation_id,
        folder_id=thread.folder_id,
        latest_run_status=runs[-1].status.value if runs else None,
        created_at=thread.created_at.replace(tzinfo=timezone.utc),
        updated_at=thread.updated_at.replace(tzinfo=timezone.utc),
        document_ids=document_ids,
        messages=[_message_response(message) for message in messages],
        plan=plan_response(plan) if plan else None,
        runs=runs,
    )


async def create_thread(
    session: AsyncSession,
    user: AppUser,
    request: CreateWorkThreadRequest,
    *,
    client_request_id: str | None = None,
    raise_on_planning_failure: bool = True,
) -> WorkThread:
    available = await run_service.capabilities(session, user)
    if not available.enabled:
        raise HTTPException(
            status_code=403,
            detail=(
                available.unavailable_reason.value
                if available.unavailable_reason
                else "work_threads_unavailable"
            ),
        )
    await _validate_context(
        session,
        user_id=user.id,
        conversation_id=request.conversation_id,
        folder_id=request.folder_id,
    )
    documents = await _owned_documents(
        session,
        user_id=user.id,
        document_ids=request.document_ids,
    )
    estimated_cost = await _reserve_planning_budget(
        session,
        goal=request.goal,
        document_count=len(documents),
    )
    thread = WorkThread(
        user_id=user.id,
        conversation_id=request.conversation_id,
        folder_id=request.folder_id,
        title=request.goal[:160],
        goal=request.goal,
        status="planning",
        context_manifest={
            "document_ids": [str(document_id) for document_id in request.document_ids],
            "output_language": request.output_language,
            **(
                {"start_client_request_id": client_request_id}
                if client_request_id
                else {}
            ),
        },
    )
    session.add(thread)
    await session.flush()
    session.add(
        WorkThreadMessage(
            thread_id=thread.id,
            role="user",
            kind="goal",
            content=request.goal,
            message_metadata=(
                {"client_request_id": client_request_id}
                if client_request_id
                else {}
            ),
        )
    )
    await session.commit()

    try:
        result = await plan_work(
            goal=request.goal,
            documents=[
                {
                    "filename": document.filename,
                    "mime_type": document.mime_type or "application/octet-stream",
                }
                for document in documents
            ],
            output_language=request.output_language,
        )
    except Exception as exc:
        thread.status = "planning_failed"
        session.add(thread)
        await session.commit()
        if not raise_on_planning_failure:
            return thread
        if isinstance(exc, WorkPlanningError):
            raise HTTPException(status_code=502, detail="work_thread_planning_failed") from exc
        raise

    actual_cost, _ = await run_service._normalization_cost(
        session,
        model=result.model,
        usage=NormalizationUsage(**result.usage),
    )
    await _store_plan(
        session,
        thread=thread,
        result=result,
        version=1,
        estimated_cost=estimated_cost,
        actual_cost=actual_cost,
    )
    thread.title = result.plan.title
    thread.status = "ready"
    session.add(thread)
    await session.commit()
    await session.refresh(thread)
    return thread


async def _store_plan(
    session: AsyncSession,
    *,
    thread: WorkThread,
    result: PlannerResult,
    version: int,
    estimated_cost: Decimal,
    actual_cost: Decimal,
) -> WorkPlan:
    plan = WorkPlan(
        thread_id=thread.id,
        version=version,
        title=result.plan.title,
        summary=result.plan.summary,
        execution_kind=result.plan.execution_kind,
        steps=[step.model_dump() for step in result.plan.steps],
        expected_outputs=[output.model_dump() for output in result.plan.expected_outputs],
        assumptions=result.plan.assumptions,
        provider="openai",
        model=result.model,
        provider_response_id=result.provider_response_id,
        usage=result.usage,
        estimated_cost_usd=estimated_cost,
        actual_cost_usd=actual_cost,
    )
    session.add(plan)
    await session.flush()
    session.add(
        WorkThreadMessage(
            thread_id=thread.id,
            role="assistant",
            kind="plan",
            content=result.plan.summary,
            message_metadata={"plan_id": str(plan.id), "plan_version": version},
        )
    )
    return plan


async def _reserve_planning_budget(
    session: AsyncSession,
    *,
    goal: str,
    document_count: int,
    context_chars: int = 0,
) -> Decimal:
    policy = (
        await session.exec(
            select(WorkRunPolicy)
            .where(WorkRunPolicy.kind == WorkRunKind.AGENTIC_TASK.value)
        )
    ).first()
    if policy is None or not policy.enabled:
        raise HTTPException(status_code=403, detail="work_threads_unavailable")
    estimate, _ = await run_service._normalization_cost(
        session,
        model="gpt-5.6-luna",
        usage=NormalizationUsage(
            input_tokens=((len(goal) + context_chars) // 2) + 1200 + document_count * 50,
            cached_input_tokens=0,
            output_tokens=3000,
            reasoning_tokens=0,
        ),
    )
    if estimate <= 0 or estimate > policy.per_run_budget_usd:
        raise HTTPException(status_code=402, detail="work_run_per_run_budget_exceeded")
    run_actual = (
        await session.exec(
            select(func.coalesce(func.sum(WorkRun.actual_cost_usd), 0)).where(
                WorkRun.created_at >= run_service._day_start()
            )
        )
    ).one()
    plan_actual = (
        await session.exec(
            select(func.coalesce(func.sum(WorkPlan.actual_cost_usd), 0)).where(
                WorkPlan.created_at >= run_service._day_start()
            )
        )
    ).one()
    if Decimal(run_actual) + Decimal(plan_actual) + estimate > policy.global_daily_budget_usd:
        raise HTTPException(status_code=402, detail="work_run_daily_budget_exceeded")
    return estimate


async def _latest_user_message(
    session: AsyncSession,
    thread_id: uuid.UUID,
) -> WorkThreadMessage | None:
    return (
        await session.exec(
            select(WorkThreadMessage)
            .where(
                WorkThreadMessage.thread_id == thread_id,
                WorkThreadMessage.role == "user",
            )
            .order_by(col(WorkThreadMessage.created_at).desc())
            .limit(1)
        )
    ).first()


async def _follow_up_context(
    session: AsyncSession,
    thread: WorkThread,
    *,
    intent: str,
) -> dict[str, object]:
    latest_result = (
        await session.exec(
            select(WorkThreadMessage)
            .where(
                WorkThreadMessage.thread_id == thread.id,
                WorkThreadMessage.role == "assistant",
                WorkThreadMessage.kind == "result",
            )
            .order_by(col(WorkThreadMessage.created_at).desc())
            .limit(1)
        )
    ).first()
    return {
        "original_goal": thread.goal,
        "previous_result": latest_result.content[-16000:] if latest_result else None,
        "follow_up_intent": intent,
    }


async def _plan_existing_thread(
    session: AsyncSession,
    *,
    thread: WorkThread,
    instruction: str,
    intent: str,
    add_message: bool,
    expected_status: str,
    conflict_detail: str,
    message_metadata: dict[str, object] | None = None,
    raise_on_planning_failure: bool = True,
) -> WorkThread:
    document_ids = [
        uuid.UUID(value) for value in thread.context_manifest.get("document_ids", [])
    ]
    documents = await _owned_documents(
        session,
        user_id=thread.user_id,
        document_ids=document_ids,
    )
    context = await _follow_up_context(session, thread, intent=intent)
    estimated_cost = await _reserve_planning_budget(
        session,
        goal=instruction,
        document_count=len(documents),
        context_chars=len(json.dumps(context, ensure_ascii=False)),
    )
    locked_thread = (
        await session.exec(
            select(WorkThread)
            .where(WorkThread.id == thread.id)
            .with_for_update()
        )
    ).one()
    if locked_thread.status != expected_status:
        raise HTTPException(status_code=409, detail=conflict_detail)
    thread = locked_thread
    current = await _latest_plan(session, thread.id)
    version = current.version + 1 if current else 1
    if current is not None and current.status == "proposed":
        current.status = "superseded"
        session.add(current)
    if add_message:
        metadata = {"intent": intent, **(message_metadata or {})}
        session.add(
            WorkThreadMessage(
                thread_id=thread.id,
                role="user",
                kind="follow_up",
                content=instruction,
                message_metadata=metadata,
            )
        )
    thread.status = "planning"
    session.add(thread)
    await session.commit()

    try:
        result = await plan_work(
            goal=instruction,
            documents=[
                {
                    "filename": document.filename,
                    "mime_type": document.mime_type or "application/octet-stream",
                }
                for document in documents
            ],
            output_language=thread.context_manifest.get("output_language", "ru"),
            context=context,
        )
    except Exception as exc:
        thread.status = "planning_failed"
        session.add(thread)
        await session.commit()
        if not raise_on_planning_failure:
            return thread
        if isinstance(exc, WorkPlanningError):
            raise HTTPException(status_code=502, detail="work_thread_planning_failed") from exc
        raise

    actual_cost, _ = await run_service._normalization_cost(
        session,
        model=result.model,
        usage=NormalizationUsage(**result.usage),
    )
    await _store_plan(
        session,
        thread=thread,
        result=result,
        version=version,
        estimated_cost=estimated_cost,
        actual_cost=actual_cost,
    )
    thread.status = "ready"
    session.add(thread)
    await session.commit()
    await session.refresh(thread)
    return thread


async def create_follow_up(
    session: AsyncSession,
    thread: WorkThread,
    request: CreateWorkFollowUpRequest,
) -> WorkThread:
    if thread.status != "completed":
        raise HTTPException(status_code=409, detail="work_thread_follow_up_unavailable")
    return await _plan_existing_thread(
        session,
        thread=thread,
        instruction=request.instruction,
        intent=request.intent,
        add_message=True,
        expected_status="completed",
        conflict_detail="work_thread_follow_up_unavailable",
    )


async def retry_plan(
    session: AsyncSession,
    thread: WorkThread,
) -> WorkThread:
    if thread.status != "planning_failed":
        raise HTTPException(status_code=409, detail="work_thread_plan_not_retryable")
    message = await _latest_user_message(session, thread.id)
    if message is None:
        raise HTTPException(status_code=409, detail="work_thread_plan_not_retryable")
    return await _plan_existing_thread(
        session,
        thread=thread,
        instruction=message.content,
        intent=str(message.message_metadata.get("intent", "continue")),
        add_message=False,
        expected_status="planning_failed",
        conflict_detail="work_thread_plan_not_retryable",
    )


async def remove_failed_thread(
    session: AsyncSession,
    thread: WorkThread,
) -> None:
    linked_runs = (
        await session.exec(
            select(func.count())
            .select_from(WorkThreadRun)
            .where(WorkThreadRun.thread_id == thread.id)
        )
    ).one()
    if thread.status != "planning_failed" or int(linked_runs) > 0:
        raise HTTPException(status_code=409, detail="work_thread_not_removable")
    await session.exec(delete(WorkPlan).where(WorkPlan.thread_id == thread.id))
    await session.exec(
        delete(WorkThreadMessage).where(WorkThreadMessage.thread_id == thread.id)
    )
    await session.delete(thread)
    await session.commit()


async def list_threads(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    offset: int,
    limit: int,
) -> WorkThreadListResponse:
    threads = (
        await session.exec(
            select(WorkThread)
            .where(WorkThread.user_id == user_id)
            .order_by(col(WorkThread.updated_at).desc())
            .offset(offset)
            .limit(limit + 1)
        )
    ).all()
    has_more = len(threads) > limit
    items = []
    for thread in threads[:limit]:
        latest_link = (
            await session.exec(
                select(WorkThreadRun)
                .where(WorkThreadRun.thread_id == thread.id)
                .order_by(col(WorkThreadRun.ordinal).desc())
                .limit(1)
            )
        ).first()
        run_status = None
        if latest_link:
            run = await session.get(WorkRun, latest_link.work_run_id)
            run_status = run.status if run else None
        items.append(
            WorkThreadSummaryResponse(
                id=thread.id,
                title=thread.title,
                goal=thread.goal,
                status=thread.status,
                conversation_id=thread.conversation_id,
                folder_id=thread.folder_id,
                latest_run_status=run_status,
                created_at=thread.created_at.replace(tzinfo=timezone.utc),
                updated_at=thread.updated_at.replace(tzinfo=timezone.utc),
            )
        )
    return WorkThreadListResponse(
        items=items,
        offset=offset,
        limit=limit,
        has_more=has_more,
    )


async def update_plan(
    session: AsyncSession,
    thread: WorkThread,
    request: UpdateWorkPlanRequest,
) -> WorkPlan:
    current = await _latest_plan(session, thread.id)
    if current is None or current.status != "proposed":
        raise HTTPException(status_code=409, detail="work_plan_not_editable")
    current.status = "superseded"
    plan = WorkPlan(
        thread_id=thread.id,
        version=current.version + 1,
        title=request.title,
        summary=request.summary,
        execution_kind=current.execution_kind,
        steps=[step.model_dump() for step in request.steps],
        expected_outputs=[output.model_dump() for output in request.expected_outputs],
        assumptions=current.assumptions,
        provider=current.provider,
        model=current.model,
        provider_response_id=current.provider_response_id,
        usage=current.usage,
        estimated_cost_usd=Decimal("0"),
        actual_cost_usd=Decimal("0"),
    )
    thread.title = request.title
    session.add(current)
    session.add(plan)
    session.add(thread)
    await session.commit()
    await session.refresh(plan)
    return plan


async def approve_plan(
    *,
    session: AsyncSession,
    user: AppUser,
    thread: WorkThread,
    plan_version: int,
    client_request_id: str,
) -> tuple[WorkPlan, WorkRun]:
    plan = await _latest_plan(session, thread.id)
    if plan is None or plan.version != plan_version:
        raise HTTPException(status_code=409, detail="work_plan_version_changed")
    existing_link = (
        await session.exec(
            select(WorkThreadRun).where(WorkThreadRun.plan_id == plan.id)
        )
    ).first()
    if existing_link:
        existing_run = await session.get(WorkRun, existing_link.work_run_id)
        if existing_run is not None:
            return plan, existing_run
    if plan.status != "proposed":
        raise HTTPException(status_code=409, detail="work_plan_not_approvable")

    latest_request = await _latest_user_message(session, thread.id)
    if latest_request is None:
        raise HTTPException(status_code=409, detail="work_plan_request_missing")

    document_ids = [
        uuid.UUID(value) for value in thread.context_manifest.get("document_ids", [])
    ]
    run_kind = WorkRunKind(plan.execution_kind)
    if run_kind == WorkRunKind.SPREADSHEET_BUILDER_XLSX and not document_ids:
        raise HTTPException(status_code=422, detail="work_plan_needs_spreadsheet_source")
    run = await run_service.create_run(
        session=session,
        user=user,
        request=CreateWorkRunRequest(
            kind=run_kind,
            conversation_id=thread.conversation_id,
            folder_id=thread.folder_id,
            document_ids=document_ids,
            instructions=latest_request.content,
            options=OfferComparisonOptions(
                output_language=thread.context_manifest.get("output_language", "ru")
            ),
        ),
        client_request_id=client_request_id,
    )
    ordinal = (
        await session.exec(
            select(func.count())
            .select_from(WorkThreadRun)
            .where(WorkThreadRun.thread_id == thread.id)
        )
    ).one()
    session.add(
        WorkThreadRun(
            thread_id=thread.id,
            work_run_id=run.id,
            plan_id=plan.id,
            ordinal=int(ordinal) + 1,
        )
    )
    plan.status = "approved"
    plan.approved_at = utcnow_naive()
    thread.status = "running"
    session.add(plan)
    session.add(thread)
    await session.commit()
    return plan, run


async def _existing_execution(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    client_request_id: str,
    thread_id: uuid.UUID | None = None,
) -> tuple[WorkThread, WorkRun] | None:
    run = (
        await session.exec(
            select(WorkRun).where(
                WorkRun.user_id == user_id,
                WorkRun.client_request_id == client_request_id,
            )
        )
    ).first()
    if run is None:
        return None
    link = (
        await session.exec(
            select(WorkThreadRun).where(WorkThreadRun.work_run_id == run.id)
        )
    ).first()
    if link is None or (thread_id is not None and link.thread_id != thread_id):
        raise HTTPException(status_code=409, detail="work_message_idempotency_conflict")
    thread = await session.get(WorkThread, link.thread_id)
    if thread is None or thread.user_id != user_id:
        raise HTTPException(status_code=409, detail="work_message_idempotency_conflict")
    return thread, run


async def start_conversation(
    *,
    session: AsyncSession,
    user: AppUser,
    request: CreateWorkThreadRequest,
    client_request_id: str,
) -> tuple[WorkThread, WorkRun | None]:
    existing = await _existing_execution(
        session,
        user_id=user.id,
        client_request_id=client_request_id,
    )
    if existing is not None:
        return existing

    recent = (
        await session.exec(
            select(WorkThread)
            .where(WorkThread.user_id == user.id)
            .order_by(col(WorkThread.created_at).desc())
            .limit(100)
        )
    ).all()
    thread = next(
        (
            item
            for item in recent
            if item.context_manifest.get("start_client_request_id")
            == client_request_id
        ),
        None,
    )
    if thread is None:
        thread = await create_thread(
            session,
            user,
            request,
            client_request_id=client_request_id,
            raise_on_planning_failure=False,
        )
    elif thread.status == "planning_failed":
        message = await _latest_user_message(session, thread.id)
        if message is not None:
            thread = await _plan_existing_thread(
                session,
                thread=thread,
                instruction=message.content,
                intent="continue",
                add_message=False,
                expected_status="planning_failed",
                conflict_detail="work_message_not_available",
                raise_on_planning_failure=False,
            )
    if thread.status != "ready":
        return thread, None
    plan = await _latest_plan(session, thread.id)
    if plan is None:
        return thread, None
    _, run = await approve_plan(
        session=session,
        user=user,
        thread=thread,
        plan_version=plan.version,
        client_request_id=client_request_id,
    )
    return thread, run


async def send_message(
    *,
    session: AsyncSession,
    user: AppUser,
    thread: WorkThread,
    request: SendWorkMessageRequest,
    client_request_id: str,
) -> tuple[WorkThread, WorkRun | None]:
    existing = await _existing_execution(
        session,
        user_id=user.id,
        client_request_id=client_request_id,
        thread_id=thread.id,
    )
    if existing is not None:
        return existing

    messages = (
        await session.exec(
            select(WorkThreadMessage).where(WorkThreadMessage.thread_id == thread.id)
        )
    ).all()
    duplicate = next(
        (
            message
            for message in messages
            if message.message_metadata.get("client_request_id")
            == client_request_id
        ),
        None,
    )
    if thread.status == "running" and request.steer_active:
        if duplicate is not None:
            return thread, None
        if request.document_ids:
            raise HTTPException(
                status_code=409,
                detail="work_steering_cannot_add_documents",
            )
        latest_link = (
            await session.exec(
                select(WorkThreadRun)
                .where(WorkThreadRun.thread_id == thread.id)
                .order_by(col(WorkThreadRun.ordinal).desc())
                .limit(1)
            )
        ).first()
        active_run = (
            await session.get(WorkRun, latest_link.work_run_id)
            if latest_link is not None
            else None
        )
        if active_run is None or active_run.status not in {
            WorkRunStatus.ACCEPTED.value,
            WorkRunStatus.RESERVED.value,
            WorkRunStatus.QUEUED.value,
            WorkRunStatus.RUNNING.value,
        }:
            raise HTTPException(status_code=409, detail="work_message_active_run")
        steering_message = WorkThreadMessage(
            thread_id=thread.id,
            role="user",
            kind="follow_up",
            content=request.content,
            message_metadata={
                "client_request_id": client_request_id,
                "steering_for_run_id": str(active_run.id),
                "steering_applied": False,
            },
        )
        session.add(steering_message)
        thread.updated_at = utcnow_naive()
        active_run.options = {
            **active_run.options,
            "steering_pending": True,
        }
        session.add(thread)
        session.add(active_run)
        await session.commit()
        return thread, None
    if thread.status in {"planning", "running"}:
        if duplicate is not None:
            return thread, None
        raise HTTPException(status_code=409, detail="work_message_active_run")
    if duplicate is None:
        current_document_ids = [
            uuid.UUID(value)
            for value in thread.context_manifest.get("document_ids", [])
        ]
        combined_document_ids = list(
            dict.fromkeys([*current_document_ids, *request.document_ids])
        )
        if len(combined_document_ids) > 5:
            raise HTTPException(status_code=422, detail="work_thread_too_many_documents")
        await _owned_documents(
            session,
            user_id=user.id,
            document_ids=combined_document_ids,
        )
        manifest = dict(thread.context_manifest)
        manifest["document_ids"] = [str(value) for value in combined_document_ids]
        thread.context_manifest = manifest
        session.add(thread)
        await session.commit()
        thread = await _plan_existing_thread(
            session,
            thread=thread,
            instruction=request.content,
            intent="continue",
            add_message=True,
            expected_status=thread.status,
            conflict_detail="work_message_not_available",
            message_metadata={
                "client_request_id": client_request_id,
                "document_ids": [str(value) for value in request.document_ids],
            },
            raise_on_planning_failure=False,
        )
    if thread.status != "ready":
        return thread, None
    plan = await _latest_plan(session, thread.id)
    if plan is None:
        return thread, None
    _, run = await approve_plan(
        session=session,
        user=user,
        thread=thread,
        plan_version=plan.version,
        client_request_id=client_request_id,
    )
    return thread, run


async def retry_conversation_turn(
    *,
    session: AsyncSession,
    user: AppUser,
    thread: WorkThread,
    client_request_id: str,
) -> tuple[WorkThread, WorkRun | None]:
    existing = await _existing_execution(
        session,
        user_id=user.id,
        client_request_id=client_request_id,
        thread_id=thread.id,
    )
    if existing is not None:
        return existing
    if (
        thread.context_manifest.get("retry_client_request_id")
        == client_request_id
        and thread.status == "planning"
    ):
        return thread, None
    if thread.status not in {"failed", "planning_failed"}:
        raise HTTPException(status_code=409, detail="work_message_not_retryable")
    message = await _latest_user_message(session, thread.id)
    if message is None:
        raise HTTPException(status_code=409, detail="work_message_not_retryable")
    manifest = dict(thread.context_manifest)
    manifest["retry_client_request_id"] = client_request_id
    thread.context_manifest = manifest
    session.add(thread)
    await session.commit()
    thread = await _plan_existing_thread(
        session,
        thread=thread,
        instruction=message.content,
        intent=str(message.message_metadata.get("intent", "continue")),
        add_message=False,
        expected_status=thread.status,
        conflict_detail="work_message_not_retryable",
        raise_on_planning_failure=False,
    )
    if thread.status != "ready":
        return thread, None
    plan = await _latest_plan(session, thread.id)
    if plan is None:
        return thread, None
    _, run = await approve_plan(
        session=session,
        user=user,
        thread=thread,
        plan_version=plan.version,
        client_request_id=client_request_id,
    )
    return thread, run
