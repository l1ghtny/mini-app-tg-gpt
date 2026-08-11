from __future__ import annotations

import uuid
from datetime import timezone
from decimal import Decimal

from fastapi import HTTPException
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
    CreateWorkThreadRequest,
    UpdateWorkPlanRequest,
    WorkPlanResponse,
    WorkThreadListResponse,
    WorkThreadMessageResponse,
    WorkThreadResponse,
    WorkThreadSummaryResponse,
)
from app.services.work_runs import service as run_service
from app.services.work_runs.contracts import WorkRunKind
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
            input_tokens=(len(goal) // 2) + 1200 + document_count * 50,
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
            instructions=thread.goal,
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
