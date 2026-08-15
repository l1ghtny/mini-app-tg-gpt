from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import timezone
from typing import Any

from fastapi import HTTPException
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import WorkRun, utcnow_naive
from app.db.work_agent_models import (
    WorkHumanInputRequest,
    WorkThread,
    WorkThreadMessage,
    WorkThreadRun,
)
from app.schemas.work_threads import WorkHumanInputRequestResponse
from app.services.work_runs.activity import record_activity_event
from app.services.work_runs.contracts import WorkRunStatus


MAX_CLARIFICATION_ROUNDS = 2
_FORBIDDEN_SECRET_TERMS = re.compile(
    r"\b(password|passcode|api[ _-]?key|secret[ _-]?key|access[ _-]?token|"
    r"refresh[ _-]?token|private[ _-]?key|credit[ _-]?card|cvv|pin[ _-]?code|"
    r"парол[ья]|api[ _-]?ключ|секретн(?:ый|ого) ключ|токен доступа|cvv|пин)\b",
    re.IGNORECASE,
)

ASK_USER_TOOL: dict[str, object] = {
    "type": "function",
    "name": "ask_user",
    "description": (
        "Ask one concise question only when missing information materially changes "
        "the correctness, evidence, cost, or a consequential action. Prefer a clearly "
        "labeled reasonable assumption for non-material ambiguity. Never request secrets."
    ),
    "strict": True,
    "parameters": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The single concise question to show the user.",
            },
            "reason": {
                "type": "string",
                "description": "Why this answer materially changes the work.",
            },
        },
        "required": ["question", "reason"],
        "additionalProperties": False,
    },
}


@dataclass(frozen=True)
class ParsedHumanInputCall:
    provider_response_id: str
    provider_call_id: str
    question: str
    reason: str


def _value(source: Any, name: str) -> Any:
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


def _clean_text(value: Any, *, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def parse_ask_user_call(response: Any) -> ParsedHumanInputCall | None:
    calls = [
        item
        for item in (_value(response, "output") or [])
        if _value(item, "type") == "function_call"
        and _value(item, "name") == "ask_user"
    ]
    if not calls:
        return None
    if len(calls) != 1:
        raise ValueError("ask_user must contain exactly one question")
    call = calls[0]
    raw_arguments = _value(call, "arguments")
    try:
        arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
    except json.JSONDecodeError as exc:
        raise ValueError("ask_user arguments are invalid") from exc
    if not isinstance(arguments, dict):
        raise ValueError("ask_user arguments are invalid")
    question = _clean_text(arguments.get("question"), limit=1000)
    reason = _clean_text(arguments.get("reason"), limit=500)
    response_id = _clean_text(_value(response, "id"), limit=128)
    call_id = _clean_text(_value(call, "call_id"), limit=128)
    if len(question) < 3 or len(reason) < 3 or not response_id or not call_id:
        raise ValueError("ask_user call is incomplete")
    if _FORBIDDEN_SECRET_TERMS.search(question) or _FORBIDDEN_SECRET_TERMS.search(
        reason
    ):
        raise ValueError("ask_user cannot request secrets")
    return ParsedHumanInputCall(
        provider_response_id=response_id,
        provider_call_id=call_id,
        question=question,
        reason=reason,
    )


def human_input_response(
    request: WorkHumanInputRequest,
) -> WorkHumanInputRequestResponse:
    return WorkHumanInputRequestResponse(
        id=request.id,
        work_run_id=request.work_run_id,
        round=request.round,
        status=request.status,
        question=request.question,
        reason=request.reason,
        answer=request.answer,
        created_at=request.created_at.replace(tzinfo=timezone.utc),
        answered_at=(
            request.answered_at.replace(tzinfo=timezone.utc)
            if request.answered_at
            else None
        ),
        resumed_at=(
            request.resumed_at.replace(tzinfo=timezone.utc)
            if request.resumed_at
            else None
        ),
    )


async def list_human_input_requests(
    session: AsyncSession,
    thread_id: uuid.UUID,
) -> list[WorkHumanInputRequest]:
    return list(
        (
            await session.exec(
                select(WorkHumanInputRequest)
                .where(WorkHumanInputRequest.thread_id == thread_id)
                .order_by(WorkHumanInputRequest.created_at)
            )
        ).all()
    )


async def clarification_round_count(
    session: AsyncSession,
    work_run_id: uuid.UUID,
) -> int:
    return int(
        (
            await session.exec(
                select(func.count())
                .select_from(WorkHumanInputRequest)
                .where(WorkHumanInputRequest.work_run_id == work_run_id)
            )
        ).one()
    )


async def answered_request_to_resume(
    session: AsyncSession,
    work_run_id: uuid.UUID,
) -> WorkHumanInputRequest | None:
    return (
        await session.exec(
            select(WorkHumanInputRequest)
            .where(
                WorkHumanInputRequest.work_run_id == work_run_id,
                WorkHumanInputRequest.status == "answered",
                WorkHumanInputRequest.resumed_at.is_(None),
            )
            .order_by(col(WorkHumanInputRequest.round).desc())
            .limit(1)
        )
    ).first()


async def pause_for_human_input(
    session: AsyncSession,
    *,
    run: WorkRun,
    thread: WorkThread,
    call: ParsedHumanInputCall,
) -> WorkHumanInputRequest:
    existing = (
        await session.exec(
            select(WorkHumanInputRequest).where(
                WorkHumanInputRequest.provider == "openai",
                WorkHumanInputRequest.provider_call_id == call.provider_call_id,
            )
        )
    ).first()
    if existing is not None:
        return existing
    round_number = await clarification_round_count(session, run.id) + 1
    if round_number > MAX_CLARIFICATION_ROUNDS:
        raise ValueError("clarification round limit reached")
    request = WorkHumanInputRequest(
        thread_id=thread.id,
        work_run_id=run.id,
        round=round_number,
        status="pending",
        question=call.question,
        reason=call.reason,
        provider="openai",
        provider_response_id=call.provider_response_id,
        provider_call_id=call.provider_call_id,
    )
    session.add(request)
    await session.flush()
    session.add(
        WorkThreadMessage(
            thread_id=thread.id,
            role="assistant",
            kind="clarification_question",
            content=call.question,
            message_metadata={
                "human_input_request_id": str(request.id),
                "work_run_id": str(run.id),
                "round": round_number,
            },
        )
    )
    now = utcnow_naive()
    run.status = WorkRunStatus.WAITING_FOR_USER.value
    run.stage = "waiting_for_user"
    run.worker_id = None
    run.lease_expires_at = None
    thread.status = "waiting_for_user"
    thread.updated_at = now
    session.add(run)
    session.add(thread)
    await record_activity_event(
        session,
        run,
        event_key=f"human_input:{request.id}",
        kind="human_input",
        status="active",
        detail=call.question,
        metadata={"request_id": str(request.id), "round": round_number},
    )
    await session.commit()
    await session.refresh(request)
    return request


async def answer_human_input(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    request_id: uuid.UUID,
    answer: str,
    idempotency_key: str,
) -> tuple[WorkHumanInputRequest, WorkRun, WorkThread]:
    run = (
        await session.exec(
            select(WorkRun)
            .where(WorkRun.id == run_id, WorkRun.user_id == user_id)
            .with_for_update()
        )
    ).first()
    request = (
        await session.exec(
            select(WorkHumanInputRequest)
            .where(
                WorkHumanInputRequest.id == request_id,
                WorkHumanInputRequest.work_run_id == run_id,
            )
            .with_for_update()
        )
    ).first()
    link = (
        await session.exec(
            select(WorkThreadRun).where(WorkThreadRun.work_run_id == run_id)
        )
    ).first()
    thread = await session.get(WorkThread, link.thread_id) if link else None
    if request is None or run is None or thread is None:
        raise HTTPException(status_code=404, detail="work_human_input_not_found")
    if request.status != "pending":
        if (
            request.answer_idempotency_key == idempotency_key
            and request.answer == answer
        ):
            return request, run, thread
        raise HTTPException(status_code=409, detail="work_human_input_already_answered")
    if run.status != WorkRunStatus.WAITING_FOR_USER.value:
        raise HTTPException(status_code=409, detail="work_run_not_waiting_for_user")
    now = utcnow_naive()
    request.status = "answered"
    request.answer = answer
    request.answer_idempotency_key = idempotency_key
    request.answered_at = now
    session.add(request)
    session.add(
        WorkThreadMessage(
            thread_id=thread.id,
            role="user",
            kind="clarification_answer",
            content=answer,
            message_metadata={
                "human_input_request_id": str(request.id),
                "work_run_id": str(run.id),
                "round": request.round,
            },
        )
    )
    run.status = WorkRunStatus.QUEUED.value
    run.stage = "waiting_for_worker"
    run.queued_at = now
    run.worker_id = None
    run.lease_expires_at = None
    thread.status = "active"
    thread.updated_at = now
    session.add(run)
    session.add(thread)
    await record_activity_event(
        session,
        run,
        event_key=f"human_input:{request.id}",
        kind="human_input",
        status="completed",
        detail=request.question,
        metadata={"request_id": str(request.id), "round": request.round},
    )
    await session.commit()
    await session.refresh(request)
    await session.refresh(run)
    return request, run, thread
