import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Optional, Sequence

from fastapi import BackgroundTasks, HTTPException, Request, Response
from redis.asyncio import Redis
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse
from sqlmodel import select, desc
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.responses import JSONResponse

from app.api.dependencies import get_available_models
from app.api.document_helpers import (
    count_conversation_pending_indexing_documents,
    list_conversation_ready_vector_store_ids,
)
from app.api.helpers import generate_and_publish, load_conversation
from app.core.config import settings as app_settings
from app.core.metrics import track_event
from app.db import models
from app.db.models import AppUser, Conversation, Message, RequestLedger, ChatFolder, TextModelCatalog
from app.redis.event_bus import RedisEventBus
from app.redis.settings import settings as redis_settings
from app.schemas.chat import (
    EditMessageRequest,
    MessageUpdated,
    MessageCreated,
    NewMessageRequest,
    RequestExists,
    RenameRequest,
    UpdateConversationSettingsRequest, ConversationInfo,
)
from app.services.background.image_deriver import (
    ensure_openai_compatible_image_url,
    rewrite_message_image_url,
)
from app.services.model_registry import (
    GOOGLE_THINKING_MODELS,
    get_default_image_model_for_provider,
    get_image_model_provider,
    get_text_model_provider,
    models_share_provider,
)
from app.services.streaming.test_idempotency import _choose_link_for_message
from app.services.subscription_check.entitlements import (
    get_daily_text_count,
    reserve_request,
    select_image_entitlement,
    select_text_entitlement,
)
from app.services.subscription_check.pacing import get_image_quality_pricing
from app.services.subscription_check.realtime_check import create_tools_list
from app.services.tasks import generate_and_save_title
from app.services.openai_service import summarize_history_chunk
from app.services.openai_chain import (
    INVALIDATING_CHAIN_REASONS,
    build_chain_context_fingerprint,
    invalidate_openai_chain_state,
    resolve_previous_response_id_for_chain,
)
from app.services.google_chain import (
    invalidate_google_chain_state,
    resolve_previous_interaction_id_for_chain,
)

_TOOL_TYPE_ALIASES = {
    "web_search_preview": "web_search",
    "web_search_preview_2025_03_11": "web_search",
    "web_search_2025_08_26": "web_search",
}
_BASIC_TOOL_CHOICES = {"auto", "none", "required"}

_DEFAULT_CONTEXT_WINDOW_TOKENS = 128000
_DEFAULT_HISTORY_BUDGET_TOKENS = 12000
_MIN_HISTORY_BUDGET_TOKENS = 2500
_RESERVED_NON_HISTORY_TOKENS = 6000
_SUMMARY_MAX_OUTPUT_TOKENS = 2000
_SUMMARY_REFRESH_MIN_NEW_TOKENS = 600
_SUMMARY_MODEL = "gpt-5.4-nano"


def _provider_mismatch_detail(*, model: str, image_model: str) -> dict[str, str]:
    return {
        "error": "provider_mismatch",
        "message": "Text and image models must use the same provider.",
        "model": model,
        "model_provider": get_text_model_provider(model),
        "image_model": image_model,
        "image_model_provider": get_image_model_provider(image_model),
    }


def _validate_or_align_image_model(
    *,
    model: str,
    image_model: str | None,
    explicit_image_model: bool,
) -> str:
    if image_model is None:
        return get_default_image_model_for_provider(get_text_model_provider(model))

    if explicit_image_model and not models_share_provider(model, image_model):
        raise HTTPException(
            status_code=400,
            detail=_provider_mismatch_detail(model=model, image_model=image_model),
        )

    if not explicit_image_model and not models_share_provider(model, image_model):
        return get_default_image_model_for_provider(get_text_model_provider(model))

    return image_model


@dataclass(frozen=True)
class TextEntitlementSelection:
    remaining: int
    tier_id: Optional[uuid.UUID]
    usage_pack_id: Optional[uuid.UUID]


@dataclass(frozen=True)
class ImageEntitlementSelection:
    allowed: bool
    tier_id: Optional[uuid.UUID]
    usage_pack_id: Optional[uuid.UUID]
    cost: float
    throttle_reason: Optional[str]
    wait_time: Optional[timedelta]


@dataclass(frozen=True)
class _HistoryCandidate:
    message_id: uuid.UUID
    payload: dict[str, Any]
    estimated_tokens: int


async def handle_create_message(
    *,
    conversation_id: uuid.UUID,
    request: NewMessageRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession,
    current_user: AppUser,
    bus: Redis,
) -> MessageCreated | RequestExists:
    idempotency_key = request.client_request_id
    existing_response = await _get_idempotency_response(
        session,
        bus,
        conversation_id,
        current_user.id,
        idempotency_key,
    )
    if existing_response:
        return existing_response

    conversation = await _load_conversation_for_user(session, conversation_id, current_user.id)

    (
        text_entitlement,
        image_entitlement,
        available_tools,
        image_model,
        image_quality,
    ) = await _check_entitlements(session, current_user, request, conversation)
    tools, request_tool_choice, ledger_tool_choice = _resolve_openai_tooling(
        request.tool_choice,
        available_tools,
    )

    resolved_system_prompt = _resolve_system_prompt(conversation, current_user)

    system_prompt = _apply_image_quota_notice(
        resolved_system_prompt,
        image_allowed=image_entitlement.allowed,
        throttle_reason=image_entitlement.throttle_reason,
        wait_time=image_entitlement.wait_time,
        image_model=image_model,
        image_quality=image_quality,
    )

    user_msg = await _create_user_message(session, conversation, request, background_tasks)
    assistant_msg = await _create_assistant_message(session, conversation_id)

    await reserve_request(
        session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        assistant_message_id=assistant_msg.id,
        request_id=idempotency_key,
        model_name=request.model,
        feature="text",
        cost=1.0,
        tool_choice=ledger_tool_choice,
        tier_id=text_entitlement.tier_id,
        usage_pack_id=text_entitlement.usage_pack_id,
    )

    full_history_for_openai = await _build_history_for_openai(
        session,
        conversation_id,
        model_name=request.model,
    )
    history_for_openai = full_history_for_openai
    fallback_history_for_openai: Optional[list[dict[str, Any]]] = None
    chain_context_fingerprint = build_chain_context_fingerprint(
        model=request.model,
        system_prompt=system_prompt,
        ledger_tool_choice=ledger_tool_choice,
        image_model=image_model,
        image_quality=image_quality,
        tools=tools,
        extract_tool_type=_extract_tool_type,
    )
    provider = get_text_model_provider(request.model)
    previous_response_id: str | None = None
    previous_interaction_id: str | None = None
    if provider == "google":
        previous_interaction_id, chain_reason = resolve_previous_interaction_id_for_chain(
            conversation,
            current_fingerprint=chain_context_fingerprint,
            chaining_enabled=app_settings.OPENAI_CHAINING_ENABLED,
            max_inactivity_days=app_settings.OPENAI_CHAIN_MAX_INACTIVITY_DAYS,
        )
    else:
        previous_response_id, chain_reason = resolve_previous_response_id_for_chain(
            conversation,
            current_fingerprint=chain_context_fingerprint,
            chaining_enabled=app_settings.OPENAI_CHAINING_ENABLED,
            max_inactivity_days=app_settings.OPENAI_CHAIN_MAX_INACTIVITY_DAYS,
        )
    if chain_reason in INVALIDATING_CHAIN_REASONS:
        invalidate_openai_chain_state(conversation)
        invalidate_google_chain_state(conversation)
        session.add(conversation)
        await session.commit()
    if previous_response_id or previous_interaction_id:
        current_turn_history = await _build_history_for_message(
            session,
            message_id=user_msg.id,
        )
        if current_turn_history:
            history_for_openai = current_turn_history
            fallback_history_for_openai = full_history_for_openai
        else:
            previous_response_id = None
            previous_interaction_id = None
            chain_reason = "missing_current_turn_payload"
    if previous_response_id or previous_interaction_id:
        background_tasks.add_task(
            track_event,
            "openai.chain.attempted",
            str(current_user.id),
            {"model": request.model},
        )
    elif app_settings.OPENAI_CHAINING_ENABLED:
        background_tasks.add_task(
            track_event,
            "openai.chain.not_used",
            str(current_user.id),
            {"model": request.model, "reason": chain_reason or "unknown"},
        )
    await bus.set(
        _conversation_current_stream_key(conversation_id),
        str(assistant_msg.id),
        ex=redis_settings.STREAM_TTL_SECONDS,
    )
    redis_bus = RedisEventBus(bus)

    _queue_generation(
        background_tasks,
        conversation_id=conversation_id,
        assistant_message_id=assistant_msg.id,
        user_id=current_user.id,
        history_for_openai=history_for_openai,
        fallback_history_for_openai=fallback_history_for_openai,
        bus=redis_bus,
        instructions=system_prompt,
        model=request.model,
        tool_choice=request_tool_choice,
        tools=tools,
        request_id=idempotency_key,
        previous_response_id=previous_response_id,
        previous_interaction_id=previous_interaction_id,
        chain_context_fingerprint=chain_context_fingerprint,
        image_entitlement_tier_id=image_entitlement.tier_id,
        image_entitlement_pack_id=image_entitlement.usage_pack_id,
        thinking_enabled=request.thinking,
        reasoning_effort=request.reasoning_effort,
    )
    await _track_message_metrics(session, background_tasks, current_user, request.model)

    return MessageCreated(
        user_message_id=user_msg.id,
        assistant_message_id=assistant_msg.id,
        message_id=assistant_msg.id,
        stream_url=f"/api/v1/conversations/{conversation_id}/messages/{assistant_msg.id}/stream",
    )


async def handle_stream_message(
    *,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    request: Request,
    bus: RedisEventBus,
    current_user: AppUser,
    last_event_id: str | None,
    session: AsyncSession,
) -> EventSourceResponse:
    await _load_conversation_for_user(session, conversation_id, current_user.id)

    start_id = last_event_id or "0-0"

    async def gen():
        async for sid, ev in bus.read(str(message_id), start_id):
            if await request.is_disconnected():
                return
            event_name = ev.get("type", "message")
            payload = ev
            yield {
                "id": sid,
                "event": event_name,
                "data": json.dumps(payload, separators=(",", ":")),
            }
            if event_name in ("done", "error"):
                return

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }
    return EventSourceResponse(gen(), headers=headers)


async def handle_delete_message(
    *,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    session: AsyncSession,
    current_user: AppUser,
) -> None:
    conversation = await _load_conversation_for_user(session, conversation_id, current_user.id)
    messages = await _load_messages_for_conversation(
        session,
        conversation_id,
        include_content=False,
    )

    target_index = _find_message_index(messages, message_id)
    if target_index is None:
        raise HTTPException(status_code=404, detail="Message not found")

    for message in messages[target_index:]:
        await session.delete(message)

    invalidate_openai_chain_state(conversation)
    invalidate_google_chain_state(conversation)
    conversation.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(conversation)
    await session.commit()


async def handle_edit_message(
    *,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    request: EditMessageRequest,
    session: AsyncSession,
    current_user: AppUser,
) -> MessageUpdated:
    conversation = await _load_conversation_for_user(session, conversation_id, current_user.id)
    messages = await _load_messages_for_conversation(
        session,
        conversation_id,
        include_content=True,
    )

    target_index = _find_message_index(messages, message_id)
    if target_index is None:
        raise HTTPException(status_code=404, detail="Message not found")

    target_message = messages[target_index]
    if target_message.role != "user":
        raise HTTPException(status_code=409, detail="Only user messages can be edited")

    await _replace_message_content(session, target_message, request)

    # Delete every message that comes after the edited one, regardless of role.
    # Use timestamp boundary instead of slice order so user+assistant messages
    # are consistently truncated even when ordering ties appear.
    messages_to_delete = (await session.exec(
        select(Message).where(
            Message.conversation_id == conversation_id,
            (
                (Message.created_at > target_message.created_at)
                | (
                    (Message.created_at == target_message.created_at)
                    & (Message.id != target_message.id)
                )
            ),
        )
    )).all()

    for message in messages_to_delete:
        await session.delete(message)

    invalidate_openai_chain_state(conversation)
    invalidate_google_chain_state(conversation)
    conversation.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(conversation)
    await session.commit()

    return MessageUpdated(
        message_id=target_message.id,
        deleted_after=len(messages_to_delete),
    )


async def handle_create_conversation(
    *,
    session: AsyncSession,
    current_user: AppUser,
    folder_id: uuid.UUID | None = None,
) -> Conversation:
    user = await session.get(models.AppUser, current_user.id)
    if not user:
        user = models.AppUser(id=current_user.id, telegram_id=current_user.telegram_id)
        session.add(user)
        await session.commit()
        await session.refresh(user)

    if folder_id is not None:
        folder = await session.get(models.ChatFolder, folder_id)
        if not folder or folder.user_id != user.id:
            raise HTTPException(status_code=404, detail="Folder not found")

    new_conversation = models.Conversation(
        title="New Chat",
        user_id=user.id,
        folder_id=folder_id,
    )
    session.add(new_conversation)
    await session.commit()
    await session.refresh(new_conversation)
    return new_conversation


async def handle_get_conversations(
    *,
    session: AsyncSession,
    current_user: AppUser,
) -> list[Conversation]:
    query = (
        select(models.Conversation)
        .where(models.Conversation.user_id == current_user.id)
        .order_by(desc(func.coalesce(models.Conversation.updated_at)).nulls_last())
    )
    conversations = await session.exec(query)
    return conversations.all()


async def handle_get_conversation_messages(
    *,
    conversation_id: uuid.UUID,
    session: AsyncSession,
    current_user: AppUser,
) -> Conversation:
    query = (
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .options(selectinload(Conversation.messages).selectinload(models.Message.content))
    )
    conversation = (await session.exec(query)).first()

    if not conversation or conversation.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    conversation.messages.sort(key=lambda m: (m.created_at is None, m.created_at))
    return conversation


async def handle_sse_conversation(
    *,
    conversation_id: uuid.UUID,
    redis: Redis,
    session: AsyncSession,
    current_user: AppUser,
) -> Response:
    await _load_conversation_for_user(session, conversation_id, current_user.id)

    message_id = await redis.get(_conversation_current_stream_key(conversation_id))
    if not message_id:
        return Response(status_code=204)
    stream_url = f"/api/v1/conversations/{conversation_id}/messages/{message_id}/stream"
    return JSONResponse(
        content={"stream_url": stream_url},
        status_code=307,
        headers={"Location": stream_url},
    )


def _conversation_current_stream_key(conversation_id: uuid.UUID | str) -> str:
    return f"conv:{conversation_id}:current"


async def handle_rename_conversation(
    *,
    conversation_id: uuid.UUID,
    request: RenameRequest,
    session: AsyncSession,
    current_user: AppUser,
) -> Conversation:
    conversation = await session.get(models.Conversation, conversation_id)
    if not conversation or conversation.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    conversation.title = request.title
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return conversation


async def handle_delete_conversation(
    *,
    conversation_id: uuid.UUID,
    session: AsyncSession,
    current_user: AppUser,
) -> None:
    conversation = await session.get(models.Conversation, conversation_id)
    if not conversation or conversation.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    await session.delete(conversation)
    await session.commit()


async def handle_update_conversation_settings(
    *,
    conversation_id: uuid.UUID,
    request: UpdateConversationSettingsRequest,
    session: AsyncSession,
    current_user: AppUser,
) -> Conversation:
    conversation = await session.get(models.Conversation, conversation_id)
    if not conversation or conversation.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    req_data = request.model_dump(exclude_unset=True)
    if "folder_id" in req_data:
        folder_id = req_data["folder_id"]
        if folder_id is not None:
             folder = await session.get(ChatFolder, folder_id)
             if not folder or folder.user_id != current_user.id:
                  raise HTTPException(status_code=404, detail="Folder not found")
        conversation.folder_id = folder_id

    if request.model is not None:
        conversation.model = request.model

    if request.model is not None:
        conversation.image_model = _validate_or_align_image_model(
            model=request.model,
            image_model=request.image_model if request.image_model is not None else conversation.image_model,
            explicit_image_model=request.image_model is not None,
        )
    elif request.image_model is not None:
        if not models_share_provider(conversation.model, request.image_model):
            raise HTTPException(
                status_code=400,
                detail=_provider_mismatch_detail(model=conversation.model, image_model=request.image_model),
            )
        conversation.image_model = request.image_model

    if request.image_quality is not None:
        conversation.image_quality = request.image_quality

    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return conversation


async def _get_idempotency_response(
    session: AsyncSession,
    bus: Redis,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    request_id: str,
) -> RequestExists | None:
    existing = await session.exec(
        select(RequestLedger).where(
            RequestLedger.user_id == user_id,
            RequestLedger.request_id == request_id,
            RequestLedger.feature == "text",
        )
    )
    ledger = existing.first()
    if not ledger or not ledger.assistant_message_id:
        return None

    user_message_id = await _find_related_user_message_id(
        session,
        conversation_id=conversation_id,
        assistant_message_id=ledger.assistant_message_id,
    )

    link = await _choose_link_for_message(
        session,
        bus,
        conversation_id,
        ledger.assistant_message_id,
        ledger.created_at,
    )
    if link["stream_url"]:
        return RequestExists(
            user_message_id=user_message_id,
            assistant_message_id=ledger.assistant_message_id,
            message_id=ledger.assistant_message_id,
            stream_url=link["stream_url"],
        )

    return RequestExists(
        user_message_id=user_message_id,
        assistant_message_id=ledger.assistant_message_id,
        message_id=ledger.assistant_message_id,
        messages_url=link["messages_url"],
    )


async def _check_entitlements(
    session: AsyncSession,
    user: AppUser,
    request: NewMessageRequest,
    conversation: Conversation,
) -> tuple[TextEntitlementSelection, ImageEntitlementSelection, list, str, str]:
    text_entitlement = await _require_text_entitlement(session, user, request.model)
    await _enforce_gpt52_safeguard(session, user, request.model)

    image_model, image_quality = _resolve_image_settings(request, conversation, request.model)
    image_entitlement = await _require_image_entitlement(
        session,
        user.id,
        image_model,
        image_quality,
    )
    pending_docs_count = await count_conversation_pending_indexing_documents(session, conversation.id)
    if pending_docs_count > 0:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "documents_indexing_in_progress",
                "pending_count": pending_docs_count,
                "message": "Wait until attached documents finish indexing before sending messages.",
            },
        )

    vector_store_ids = await list_conversation_ready_vector_store_ids(session, conversation.id)
    model_provider = get_text_model_provider(request.model)
    if _is_file_search_requested(request.tool_choice) and model_provider != "openai":
        raise HTTPException(
            status_code=409,
            detail={
                "error": "file_search_not_supported_for_provider",
                "provider": model_provider,
                "model": request.model,
                "message": "File search is currently only supported for OpenAI models.",
            },
        )

    _validate_reasoning_controls(request)

    tools = await _build_tools(
        image_entitlement.allowed,
        image_model,
        image_quality,
        vector_store_ids=vector_store_ids,
        provider=model_provider,
    )

    if _is_image_generation_requested(request.tool_choice) and not image_entitlement.allowed:
        _raise_image_entitlement_error(image_entitlement, image_model, image_quality)
    if _is_file_search_requested(request.tool_choice) and not vector_store_ids:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "file_search_requires_documents",
                "message": "Attach at least one ready document to use file search.",
            },
        )

    return text_entitlement, image_entitlement, tools, image_model, image_quality


def _validate_reasoning_controls(request: NewMessageRequest) -> None:
    # Backward compatibility: legacy/frontend clients may always send the
    # boolean `thinking` toggle. For models without explicit reasoning controls
    # we accept and ignore this value instead of hard-failing the request.
    if request.reasoning_effort is not None and request.model not in GOOGLE_THINKING_MODELS:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "reasoning_effort_not_supported_for_model",
                "model": request.model,
            },
        )


def _normalize_tool_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.strip().lower()
    if not raw:
        return None
    return _TOOL_TYPE_ALIASES.get(raw, raw)


def _extract_tool_type(tool: Any) -> str | None:
    if isinstance(tool, dict):
        tool_type = tool.get("type")
    else:
        tool_type = getattr(tool, "type", None)
    return tool_type if isinstance(tool_type, str) and tool_type else None


def _serialize_tool_choice_for_ledger(tool_choice: Any) -> str:
    if isinstance(tool_choice, list):
        normalized = []
        seen = set()
        for item in tool_choice:
            tool_name = _normalize_tool_name(item)
            if not tool_name or tool_name in seen:
                continue
            seen.add(tool_name)
            normalized.append(tool_name)
        return ",".join(normalized) if normalized else "none"

    normalized = _normalize_tool_name(tool_choice)
    return normalized or "auto"


def _is_image_generation_requested(tool_choice: Any) -> bool:
    if isinstance(tool_choice, list):
        return any(_normalize_tool_name(item) == "image_generation" for item in tool_choice)
    return _normalize_tool_name(tool_choice) == "image_generation"


def _is_file_search_requested(tool_choice: Any) -> bool:
    if isinstance(tool_choice, list):
        return any(_normalize_tool_name(item) == "file_search" for item in tool_choice)
    return _normalize_tool_name(tool_choice) == "file_search"


def _resolve_openai_tooling(
    request_tool_choice: Any,
    available_tools: list,
) -> tuple[list, str | dict[str, Any], str]:
    ledger_choice = _serialize_tool_choice_for_ledger(request_tool_choice)

    tool_by_type: dict[str, Any] = {}
    for tool in available_tools:
        tool_type = _extract_tool_type(tool)
        normalized_type = _normalize_tool_name(tool_type)
        if normalized_type and normalized_type not in tool_by_type:
            tool_by_type[normalized_type] = tool

    default_tools = list(tool_by_type.values())
    if not default_tools:
        return [], "none", ledger_choice

    if isinstance(request_tool_choice, list):
        normalized_choices = []
        seen_choices = set()
        for choice in request_tool_choice:
            normalized = _normalize_tool_name(choice)
            if not normalized or normalized in seen_choices:
                continue
            seen_choices.add(normalized)
            normalized_choices.append(normalized)

        if not normalized_choices:
            return [], "none", ledger_choice

        selected_tools = [tool_by_type[name] for name in normalized_choices if name in tool_by_type]
        if not selected_tools:
            return default_tools, "auto", ledger_choice

        allowed_tool_defs = []
        for tool in selected_tools:
            tool_type = _extract_tool_type(tool)
            if tool_type:
                allowed_tool_defs.append({"type": tool_type})

        if not allowed_tool_defs:
            return selected_tools, "auto", ledger_choice

        return (
            selected_tools,
            {"type": "allowed_tools", "mode": "auto", "tools": allowed_tool_defs},
            ledger_choice,
        )

    normalized_choice = _normalize_tool_name(request_tool_choice)

    if normalized_choice in _BASIC_TOOL_CHOICES:
        return default_tools, normalized_choice, ledger_choice

    if normalized_choice and normalized_choice in tool_by_type:
        selected_tool = tool_by_type[normalized_choice]
        selected_type = _extract_tool_type(selected_tool)
        if selected_type:
            return (
                [selected_tool],
                {"type": "allowed_tools", "mode": "required", "tools": [{"type": selected_type}]},
                ledger_choice,
            )
        return [selected_tool], "required", ledger_choice

    return default_tools, "auto", ledger_choice


def _raise_image_entitlement_error(
    image_entitlement: ImageEntitlementSelection,
    image_model: str,
    image_quality: str,
) -> None:
    if image_entitlement.throttle_reason == "pacing":
        wait_seconds = (
            int(image_entitlement.wait_time.total_seconds())
            if image_entitlement.wait_time
            else 0
        )
        raise HTTPException(
            status_code=429,
            detail={"error": "image_pacing_active", "wait_seconds": wait_seconds},
        )
    if image_entitlement.throttle_reason == "quality_restricted":
        raise HTTPException(
            status_code=403,
            detail={
                "error": "image_quality_not_allowed",
                "requested_quality": image_quality,
            },
        )
    if image_entitlement.throttle_reason == "model_restricted":
        raise HTTPException(
            status_code=403,
            detail={
                "error": "image_model_not_allowed",
                "requested_model": image_model,
            },
        )
    raise HTTPException(status_code=402, detail="image_quota_exceeded")


async def _require_text_entitlement(
    session: AsyncSession,
    user: AppUser,
    model: str,
) -> TextEntitlementSelection:
    text_entitlement = await select_text_entitlement(session, user.id, model)
    remaining = text_entitlement["remaining"]
    if remaining != -1 and remaining <= 0:
        available_models = await get_available_models(user, session)
        if not available_models:
            raise HTTPException(status_code=402, detail="No text usage available")
        raise HTTPException(
            status_code=409,
            detail={
                "error": "model_quota_exceeded",
                "requested_model": model,
                "available_models": available_models,
            },
        )

    tier_id = uuid.UUID(text_entitlement["tier_id"]) if text_entitlement.get("tier_id") else None
    usage_pack_id = (
        uuid.UUID(text_entitlement["usage_pack_id"])
        if text_entitlement.get("usage_pack_id")
        else None
    )
    return TextEntitlementSelection(
        remaining=remaining,
        tier_id=tier_id,
        usage_pack_id=usage_pack_id,
    )


async def _enforce_gpt52_safeguard(
    session: AsyncSession,
    user: AppUser,
    model: str,
) -> None:
    if model != "gpt-5.2":
        return

    safeguard_limit = 150
    daily_usage = await get_daily_text_count(session, user.id, model)
    if daily_usage < safeguard_limit:
        return

    available_models = await get_available_models(user, session)
    raise HTTPException(
        status_code=429,
        detail={
            "error": "model_quota_exceeded",
            "requested_model": model,
            "available_models": available_models,
        },
    )


async def _load_conversation_for_user(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Conversation:
    query = (
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .options(selectinload(Conversation.folder))
    )
    conversation = (await session.exec(query)).first()

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if conversation.user_id != user_id:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to send messages to this conversation",
        )
    return conversation


async def _load_messages_for_conversation(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    include_content: bool,
) -> list[Message]:
    query = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc(), Message.id.asc())
    )
    if include_content:
        query = query.options(selectinload(Message.content))
    return (await session.exec(query)).all()


async def _find_related_user_message_id(
    session: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    assistant_message_id: uuid.UUID,
) -> uuid.UUID | None:
    messages = await _load_messages_for_conversation(
        session,
        conversation_id,
        include_content=False,
    )
    target_index = _find_message_index(messages, assistant_message_id)
    if target_index is None:
        return None

    for idx in range(target_index - 1, -1, -1):
        message = messages[idx]
        if message.role == "user":
            return message.id
    return None


def _find_message_index(messages: list[Message], message_id: uuid.UUID) -> int | None:
    for idx, message in enumerate(messages):
        if message.id == message_id:
            return idx
    return None


async def _replace_message_content(
    session: AsyncSession,
    message: Message,
    request: EditMessageRequest,
) -> None:
    text_value = request.content
    has_text = bool(text_value and text_value.strip())
    image_values = [image for image in (request.images or []) if image]
    if not has_text and not image_values:
        raise HTTPException(status_code=400, detail="Edited message must include text or images")

    for part in list(message.content):
        await session.delete(part)

    ordinal = 0
    if has_text:
        session.add(
            models.MessageContent(
                message_id=message.id,
                ordinal=ordinal,
                type="text",
                value=text_value,
            )
        )
        ordinal += 1

    for image_url in image_values:
        session.add(
            models.MessageContent(
                message_id=message.id,
                ordinal=ordinal,
                type="image_url",
                value=image_url,
            )
        )
        ordinal += 1


def _resolve_image_settings(
    request: NewMessageRequest,
    conversation: Conversation,
    text_model: str,
) -> tuple[str, str]:
    image_model = _validate_or_align_image_model(
        model=text_model,
        image_model=request.image_model or conversation.image_model,
        explicit_image_model=request.image_model is not None,
    )
    image_quality = request.image_quality or conversation.image_quality or "low"
    return image_model, image_quality


async def _require_image_entitlement(
    session: AsyncSession,
    user_id: uuid.UUID,
    image_model: str,
    image_quality: str,
) -> ImageEntitlementSelection:
    pricing = await get_image_quality_pricing(session, image_model, image_quality)
    if not pricing:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "image_quality_unavailable",
                "requested_model": image_model,
                "requested_quality": image_quality,
            },
        )

    image_entitlement = await select_image_entitlement(
        session,
        user_id,
        image_model,
        image_quality,
    )
    allowed = image_entitlement.get("allowed", image_entitlement.get("source") != "none")
    tier_id = uuid.UUID(image_entitlement["tier_id"]) if image_entitlement.get("tier_id") else None
    usage_pack_id = (
        uuid.UUID(image_entitlement["usage_pack_id"])
        if image_entitlement.get("usage_pack_id")
        else None
    )
    cost = image_entitlement.get("cost") or pricing.credit_cost or 1.0
    return ImageEntitlementSelection(
        allowed=allowed,
        tier_id=tier_id,
        usage_pack_id=usage_pack_id,
        cost=cost,
        throttle_reason=image_entitlement.get("throttle_reason"),
        wait_time=image_entitlement.get("wait_time"),
    )


async def _build_tools(
    image_allowed: bool,
    image_model: str,
    image_quality: str,
    vector_store_ids: list[str] | None = None,
    provider: str = "openai",
):
    return await create_tools_list(
        image_allowed,
        image_model=image_model,
        image_quality=image_quality,
        vector_store_ids=vector_store_ids,
        provider=provider,
    )


def _format_wait_time(wait_time: timedelta) -> str:
    total_seconds = max(0, int(wait_time.total_seconds()))
    total_minutes = max(1, (total_seconds + 59) // 60)
    hours = total_minutes // 60
    minutes = total_minutes % 60
    if hours and minutes:
        return f"{hours} hours {minutes} minutes"
    if hours:
        return f"{hours} hours"
    return f"{minutes} minutes"

def _is_effectively_no_refill_wait(wait_time: timedelta | None) -> bool:
    if not wait_time:
        return False
    return wait_time.total_seconds() >= 30 * 24 * 60 * 60


def _apply_image_quota_notice(
    system_prompt: str | None,
    image_allowed: bool,
    throttle_reason: str | None = None,
    wait_time: timedelta | None = None,
    image_model: str | None = None,
    image_quality: str | None = None,
) -> str:
    prompt = system_prompt or ""
    if image_allowed:
        return prompt

    if throttle_reason == "pacing":
        if _is_effectively_no_refill_wait(wait_time):
            return (
                prompt
                + "\n\nSYSTEM NOTICE: The user has exhausted a one-time image energy pool that does not auto-refill. "
                "The image generation tool is disabled until they upgrade or buy additional usage. "
                "If the user asks for an image, explain they need to upgrade to continue image generation.\n\n"
            )
        time_str = _format_wait_time(wait_time) if wait_time else "a short while"
        return (
            prompt
            + "\n\nSYSTEM NOTICE: The user is generating images too quickly. "
            "The image generation tool is temporarily disabled to maintain a fair usage average. "
            f"It will be available again in approximately {time_str}. "
            "If the user asks for an image, explain that they are generating too quickly and need to wait a bit.\n\n"
        )

    if throttle_reason == "quality_restricted":
        quality_label = image_quality or "requested"
        return (
            prompt
            + f"\n\nSYSTEM NOTICE: The requested image quality '{quality_label}' is not included in the user's plan. "
            "The image generation tool is disabled for this request. "
            "If the user asks for that quality, explain that they need to upgrade their subscription tier to access it.\n\n"
        )

    if throttle_reason == "model_restricted":
        model_label = image_model or "requested"
        return (
            prompt
            + f"\n\nSYSTEM NOTICE: The requested image model '{model_label}' is not included in the user's plan. "
            "The image generation tool is disabled for this request. "
            "If the user asks for that model, explain that they need to upgrade their subscription tier to access it.\n\n"
        )

    return (
        prompt
        + "\n\nSYSTEM NOTICE: The user has used up their image generation quota. "
        "The image generation tool has been disabled. "
        "If the user wants you to generate an image, you have to explicitly tell them they have reached their image limit "
        "and need to buy more usage or wait for their quota to refill. "
        "Tell the user to click on their profile in the sidebar menu and purchase additional usage packs. "
        "Don't suggest prompts for usage in other apps.\n\n"
    )


def _resolve_system_prompt(conversation: Conversation, user: AppUser) -> str:
    parts: list[str] = []

    main_user_prompt = (user.default_prompt or "").strip()
    if main_user_prompt:
        parts.append("Main user prompt:\n\n" + main_user_prompt)

    if conversation.folder and conversation.folder.prompt:
        folder_prompt = conversation.folder.prompt.strip()
        if folder_prompt:
            parts.append("Folder prompt for this chat:\n\n" + folder_prompt)

    if not parts:
        return ""

    return "\n\n".join(parts) + "\n\n"


async def _create_user_message(
    session: AsyncSession,
    conversation: Conversation,
    request: NewMessageRequest,
    background_tasks: BackgroundTasks,
) -> Message:
    user_msg = models.Message(conversation_id=conversation.id, role="user")
    session.add(user_msg)
    await session.flush()

    conversation.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(conversation)

    for part in request.content:
        mc = models.MessageContent(message_id=user_msg.id, type=part.type, value=part.value)
        session.add(mc)
        if part.type == "text":
            background_tasks.add_task(generate_and_save_title, conversation.id, part.value)

    if getattr(conversation, "model", None) != request.model:
        conversation.model = request.model
        session.add(conversation)

    await session.commit()
    await session.refresh(user_msg)
    return user_msg


async def _create_assistant_message(
    session: AsyncSession,
    conversation_id: uuid.UUID,
) -> Message:
    assistant_msg = models.Message(conversation_id=conversation_id, role="assistant")
    session.add(assistant_msg)
    await session.commit()
    await session.refresh(assistant_msg)
    return assistant_msg


async def _build_history_for_openai(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    model_name: str | None = None,
) -> list[dict]:
    result = await session.exec(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .options(selectinload(Message.content))
        .order_by(Message.created_at.asc(), Message.id.asc())
    )
    msgs = result.all()
    candidates = [_build_history_candidate(msg) for msg in msgs]
    candidates = [candidate for candidate in candidates if candidate is not None]
    if not candidates:
        return []

    context_window = await _resolve_context_window_tokens(session, model_name)
    history_budget = _compute_history_budget_tokens(context_window)

    selected_reversed: list[_HistoryCandidate] = []
    selected_tokens = 0
    for candidate in reversed(candidates):
        would_overflow = selected_tokens + candidate.estimated_tokens > history_budget
        if would_overflow and selected_reversed:
            break
        selected_reversed.append(candidate)
        selected_tokens += candidate.estimated_tokens
        if would_overflow:
            break

    selected = list(reversed(selected_reversed))
    dropped_count = len(candidates) - len(selected)
    dropped = candidates[:dropped_count] if dropped_count > 0 else []

    summary_item = await _maybe_refresh_and_build_summary_item(
        session=session,
        conversation_id=conversation_id,
        dropped_candidates=dropped,
    )

    history: list[dict[str, Any]] = []
    if summary_item:
        history.append(summary_item)

    for candidate in selected:
        finalized = await _finalize_history_payload(session, candidate)
        if finalized:
            history.append(finalized)

    return history


async def _build_history_for_message(
    session: AsyncSession,
    *,
    message_id: uuid.UUID,
) -> list[dict[str, Any]]:
    message = (
        await session.exec(
            select(Message)
            .where(Message.id == message_id)
            .options(selectinload(Message.content))
        )
    ).first()
    if not message:
        return []

    candidate = _build_history_candidate(message)
    if candidate is None:
        return []

    payload = await _finalize_history_payload(session, candidate)
    return [payload] if payload else []


def _estimate_tokens_from_text(value: str) -> int:
    return max(1, (len(value) + 3) // 4)


def _estimate_part_tokens(part: dict[str, Any]) -> int:
    part_type = part.get("type")
    if part_type in {"input_text", "output_text"}:
        return _estimate_tokens_from_text(part.get("text", "")) + 4
    if part_type == "input_image":
        # Image references are expensive in practice; use a conservative estimate.
        return 300
    return 8


def _estimate_message_tokens(payload: dict[str, Any]) -> int:
    content = payload.get("content", [])
    return 8 + sum(_estimate_part_tokens(part) for part in content if isinstance(part, dict))


def _build_history_candidate(msg: Message) -> _HistoryCandidate | None:
    parts: list[dict[str, Any]] = []
    assistant_has_image = False

    ordered_content = sorted(msg.content, key=lambda c: (c.ordinal, c.id))
    for c in ordered_content:
        if c.type == "text" and msg.role == "user":
            parts.append({"type": "input_text", "text": c.value})
        elif c.type == "text" and msg.role == "assistant":
            parts.append({"type": "output_text", "text": c.value})
        elif (c.type in {"image_url", "image"}) and msg.role == "user":
            parts.append({"type": "input_image", "image_url": c.value})
        elif (c.type in {"image_url", "image"}) and msg.role == "assistant":
            assistant_has_image = True

    if assistant_has_image and not any(part.get("type") == "output_text" for part in parts):
        parts.append({"type": "output_text", "text": "[Generated an image.]"})

    if not parts:
        return None

    payload = {"role": msg.role, "content": parts}
    return _HistoryCandidate(
        message_id=msg.id,
        payload=payload,
        estimated_tokens=_estimate_message_tokens(payload),
    )


async def _finalize_history_payload(
    session: AsyncSession,
    candidate: _HistoryCandidate,
) -> dict[str, Any]:
    payload = {"role": candidate.payload["role"], "content": []}
    for part in candidate.payload.get("content", []):
        if part.get("type") != "input_image":
            payload["content"].append(part)
            continue

        source_url = part.get("image_url")
        if not source_url:
            continue
        compatible_url = await ensure_openai_compatible_image_url(session, source_url, max_size=2048)
        payload["content"].append({"type": "input_image", "image_url": compatible_url})
        if compatible_url != source_url:
            await rewrite_message_image_url(session, source_url, compatible_url, message_id=candidate.message_id)

    return payload


async def _resolve_context_window_tokens(session: AsyncSession, model_name: str | None) -> int:
    if not model_name:
        return _DEFAULT_CONTEXT_WINDOW_TOKENS

    row = (
        await session.exec(
            select(TextModelCatalog)
            .where(
                TextModelCatalog.model_name == model_name,
                TextModelCatalog.is_active == True,
            )
        )
    ).first()
    if row and row.context_window:
        return row.context_window
    return _DEFAULT_CONTEXT_WINDOW_TOKENS


def _compute_history_budget_tokens(context_window: int) -> int:
    budget_after_reserve = max(_MIN_HISTORY_BUDGET_TOKENS, context_window - _RESERVED_NON_HISTORY_TOKENS)
    return min(_DEFAULT_HISTORY_BUDGET_TOKENS, budget_after_reserve)


async def _maybe_refresh_and_build_summary_item(
    *,
    session: AsyncSession,
    conversation_id: uuid.UUID,
    dropped_candidates: list[_HistoryCandidate],
) -> dict[str, Any] | None:
    if not dropped_candidates:
        return None

    conversation = await session.get(Conversation, conversation_id)
    if not conversation:
        return None

    summary_text = (conversation.history_summary or "").strip()
    current_up_to = conversation.history_summary_up_to_message_id
    dropped_token_total = sum(c.estimated_tokens for c in dropped_candidates)

    start_index = 0
    if current_up_to is not None:
        for idx, candidate in enumerate(dropped_candidates):
            if candidate.message_id == current_up_to:
                start_index = idx + 1
                break

    unsummarized_candidates = dropped_candidates[start_index:]
    unsummarized_tokens = sum(c.estimated_tokens for c in unsummarized_candidates)
    newest_dropped_id = dropped_candidates[-1].message_id

    should_refresh = (
        bool(unsummarized_candidates)
        and (not summary_text or unsummarized_tokens >= _SUMMARY_REFRESH_MIN_NEW_TOKENS)
    )
    if should_refresh:
        refreshed = await summarize_history_chunk(
            previous_summary=summary_text,
            history_chunk=[candidate.payload for candidate in unsummarized_candidates],
            model=_SUMMARY_MODEL,
            max_output_tokens=_SUMMARY_MAX_OUTPUT_TOKENS,
        )
        refreshed = (refreshed or "").strip()
        if refreshed and (refreshed != summary_text or current_up_to != newest_dropped_id):
            conversation.history_summary = refreshed
            conversation.history_summary_up_to_message_id = newest_dropped_id
            conversation.history_summary_updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            session.add(conversation)
            await session.commit()
            summary_text = refreshed
            current_up_to = newest_dropped_id

    if not summary_text:
        return None

    approx_summary_tokens = _estimate_tokens_from_text(summary_text)
    if approx_summary_tokens > _SUMMARY_MAX_OUTPUT_TOKENS * 2 and dropped_token_total < _SUMMARY_REFRESH_MIN_NEW_TOKENS:
        # If the existing summary is too long but there is not enough fresh history to justify
        # another summarization call, skip injecting it to avoid overspending.
        return None

    return {
        "role": "system",
        "content": [
            {
                "type": "input_text",
                "text": "Conversation summary (older context):\n" + summary_text,
            }
        ],
    }


def _queue_generation(
    background_tasks: BackgroundTasks,
    *,
    conversation_id: uuid.UUID,
    assistant_message_id: uuid.UUID,
    user_id: uuid.UUID,
    history_for_openai: list[dict],
    fallback_history_for_openai: Optional[list[dict[str, Any]]],
    bus: RedisEventBus,
    instructions: str | None,
    model: str,
    tool_choice: str | dict[str, Any] | None,
    tools: list,
    request_id: str,
    previous_response_id: Optional[str],
    previous_interaction_id: Optional[str],
    chain_context_fingerprint: Optional[str],
    image_entitlement_tier_id: Optional[uuid.UUID],
    image_entitlement_pack_id: Optional[uuid.UUID],
    thinking_enabled: Optional[bool],
    reasoning_effort: Optional[str],
) -> None:
    background_tasks.add_task(
        generate_and_publish,
        conversation_id=conversation_id,
        assistant_message_id=assistant_message_id,
        user_id=user_id,
        history_for_openai=history_for_openai,
        fallback_history_for_openai=fallback_history_for_openai,
        bus=bus,
        instructions=instructions,
        model=model,
        tool_choice=tool_choice,
        tools=tools,
        request_id=request_id,
        previous_response_id=previous_response_id,
        previous_interaction_id=previous_interaction_id,
        chain_context_fingerprint=chain_context_fingerprint,
        image_entitlement_tier_id=image_entitlement_tier_id,
        image_entitlement_pack_id=image_entitlement_pack_id,
        thinking_enabled=thinking_enabled,
        reasoning_effort=reasoning_effort,
    )


async def _track_message_metrics(
    session: AsyncSession,
    background_tasks: BackgroundTasks,
    user: AppUser,
    model: str,
) -> None:
    message_tags = {
        "model": model,
        "telegram_username": user.telegram_username,
        "telegram_name": _telegram_display_name(user),
    }

    if not user.has_sent_first_message:
        user.has_sent_first_message = True
        session.add(user)
        await session.commit()
        await session.refresh(user)

        background_tasks.add_task(
            track_event,
            "user_activated",
            str(user.id),
            {
                "campaign": user.campaign or "organic",
                "model": model,
                "telegram_username": user.telegram_username,
                "telegram_name": _telegram_display_name(user),
            },
        )

    background_tasks.add_task(
        track_event,
        "message_sent",
        str(user.id),
        message_tags,
    )


def _telegram_display_name(user: AppUser) -> str | None:
    first = (user.telegram_first_name or "").strip()
    last = (user.telegram_last_name or "").strip()
    full = f"{first} {last}".strip()
    return full or None

async def handle_get_conversation(
    *,
    conversation_id: uuid.UUID,
    session: AsyncSession,
    current_user: AppUser,
) -> ConversationInfo:
    conversation = (
        await session.exec(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == current_user.id,
            )
        )
    ).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return ConversationInfo(
        name=conversation.title,
        folder_id=conversation.folder_id,
        model=conversation.model,
        image_model=conversation.image_model,
        image_quality=conversation.image_quality,
    )


async def handle_conversation_search(
    *,
    query: str,
    session: AsyncSession,
    current_user: AppUser,
) -> Sequence[Conversation]:
    return (
        await session.exec(
            select(Conversation).where(
                Conversation.user_id == current_user.id,
                Conversation.title.ilike(f"%{query}%"),
            )
        )
    ).all()
