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
from starlette.responses import RedirectResponse

from app.api.dependencies import get_available_models
from app.api.helpers import generate_and_publish, load_conversation
from app.core.metrics import track_event
from app.db import models
from app.db.models import AppUser, Conversation, Message, RequestLedger, ChatFolder
from app.redis.event_bus import RedisEventBus
from app.schemas.chat import (
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

_TOOL_TYPE_ALIASES = {
    "web_search_preview": "web_search",
    "web_search_preview_2025_03_11": "web_search",
    "web_search_2025_08_26": "web_search",
}
_BASIC_TOOL_CHOICES = {"auto", "none", "required"}


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

    await _create_user_message(session, conversation, request, background_tasks)
    assistant_msg = await _create_assistant_message(session, conversation_id)

    await reserve_request(
        session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        assistant_message_id=assistant_msg.id,
        request_id=idempotency_key,
        model_name=request.model,
        feature="text",
        cost=image_entitlement.cost,
        tool_choice=ledger_tool_choice,
        tier_id=text_entitlement.tier_id,
        usage_pack_id=text_entitlement.usage_pack_id,
    )

    history_for_openai = await _build_history_for_openai(session, conversation_id)
    redis_bus = RedisEventBus(bus)

    _queue_generation(
        background_tasks,
        conversation_id=conversation_id,
        assistant_message_id=assistant_msg.id,
        user_id=current_user.id,
        history_for_openai=history_for_openai,
        bus=redis_bus,
        instructions=system_prompt,
        model=request.model,
        tool_choice=request_tool_choice,
        tools=tools,
        request_id=idempotency_key,
        image_entitlement_tier_id=image_entitlement.tier_id,
        image_entitlement_pack_id=image_entitlement.usage_pack_id,
    )

    await _track_message_metrics(session, background_tasks, current_user, request.model)

    return MessageCreated(
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


async def handle_create_conversation(
    *,
    session: AsyncSession,
    current_user: AppUser,
) -> Conversation:
    user = await session.get(models.AppUser, current_user.id)
    if not user:
        user = models.AppUser(id=current_user.id, telegram_id=current_user.telegram_id)
        session.add(user)
        await session.commit()
        await session.refresh(user)

    new_conversation = models.Conversation(title="New Chat", user_id=user.id)
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
) -> Response:
    message_id = await redis.get(f"conv:{conversation_id}:current")
    if not message_id:
        return Response(status_code=204)
    return RedirectResponse(
        url=f"/api/v1/conversations/{conversation_id}/messages/{message_id}/stream",
        status_code=307,
    )


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

    if request.image_model is not None:
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

    link = await _choose_link_for_message(
        session,
        bus,
        conversation_id,
        ledger.assistant_message_id,
        ledger.created_at,
    )
    if link["stream_url"]:
        return RequestExists(
            message_id=link["message_id"],
            stream_url=link["stream_url"],
        )

    return RequestExists(
        message_id=link["message_id"],
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

    image_model, image_quality = _resolve_image_settings(request, conversation)
    image_entitlement = await _require_image_entitlement(
        session,
        user.id,
        image_model,
        image_quality,
    )
    tools = await _build_tools(
        image_entitlement.allowed,
        image_model,
        image_quality,
    )

    if _is_image_generation_requested(request.tool_choice) and not image_entitlement.allowed:
        _raise_image_entitlement_error(image_entitlement, image_model, image_quality)

    return text_entitlement, image_entitlement, tools, image_model, image_quality


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
    if text_entitlement["remaining"] <= 0:
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
        remaining=text_entitlement["remaining"],
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
    # Eagerly load folder to access its prompt
    query = (
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .options(selectinload(Conversation.folder))
    )
    conversation = (await session.exec(query)).first()

    if not conversation:
        # Check standard load if not found, or just raise 404
        # Original logic raised 403 if found but wrong user.
        # But here I'm querying by ID only first to check existence?
        # No, simpler: Query by ID, then check user_id.
        pass

    if not conversation:
         raise HTTPException(status_code=404, detail="Conversation not found")

    if conversation.user_id != user_id:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to send messages to this conversation",
        )
    return conversation


def _resolve_image_settings(request: NewMessageRequest, conversation: Conversation) -> tuple[str, str]:
    image_model = request.image_model or conversation.image_model or "gpt-image-1.5"
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
):
    return await create_tools_list(
        image_allowed,
        image_model=image_model,
        image_quality=image_quality,
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
    if conversation.folder and conversation.folder.prompt:
        return conversation.folder.prompt
    return user.default_prompt


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
) -> list[dict]:
    history = []
    result = await session.exec(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .options(selectinload(Message.content))
        .order_by(Message.created_at.asc())
    )
    msgs = result.all()
    for msg in msgs:
        parts = []
        for c in msg.content:
            if c.type == "text" and msg.role == "user":
                parts.append({"type": "input_text", "text": c.value})
            elif c.type == "text" and msg.role == "assistant":
                parts.append({"type": "output_text", "text": c.value})
            elif (c.type in ("image_url", "image")) and msg.role == "user":
                compatible_url = await ensure_openai_compatible_image_url(session, c.value, max_side=2048)
                parts.append({"type": "input_image", "image_url": compatible_url})
                if compatible_url != c.value:
                    await rewrite_message_image_url(session, c.value, compatible_url, message_id=msg.id)
        history.append({"role": msg.role, "content": parts})
    return history


def _queue_generation(
    background_tasks: BackgroundTasks,
    *,
    conversation_id: uuid.UUID,
    assistant_message_id: uuid.UUID,
    user_id: uuid.UUID,
    history_for_openai: list[dict],
    bus: RedisEventBus,
    instructions: str | None,
    model: str,
    tool_choice: str | dict[str, Any] | None,
    tools: list,
    request_id: str,
    image_entitlement_tier_id: Optional[uuid.UUID],
    image_entitlement_pack_id: Optional[uuid.UUID],
) -> None:
    background_tasks.add_task(
        generate_and_publish,
        conversation_id=conversation_id,
        assistant_message_id=assistant_message_id,
        user_id=user_id,
        history_for_openai=history_for_openai,
        bus=bus,
        instructions=instructions,
        model=model,
        tool_choice=tool_choice,
        tools=tools,
        request_id=request_id,
        image_entitlement_tier_id=image_entitlement_tier_id,
        image_entitlement_pack_id=image_entitlement_pack_id,
    )


async def _track_message_metrics(
    session: AsyncSession,
    background_tasks: BackgroundTasks,
    user: AppUser,
    model: str,
) -> None:
    if not user.has_sent_first_message:
        user.has_sent_first_message = True
        session.add(user)
        await session.commit()
        await session.refresh(user)

        background_tasks.add_task(
            track_event,
            "user_activated",
            str(user.id),
            {"campaign": user.campaign or "organic", "model": model},
        )

    background_tasks.add_task(
        track_event,
        "message_sent",
        str(user.id),
        {"model": model},
    )

async def handle_get_conversation(conversation_id: uuid.UUID, session: AsyncSession) -> ConversationInfo:
    async with session:
        conversation = (await session.exec(select(Conversation).where(Conversation.id == conversation_id))).first()
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
        conversation_info = ConversationInfo(
            name=conversation.title,
            folder_id=conversation.folder_id,
            model=conversation.model,
            image_model=conversation.image_model,
            image_quality=conversation.image_quality
        )
        return conversation_info

async def handle_conversation_search(query: str, session: AsyncSession) -> Sequence[Conversation]:
    async with session:
        result = (await session.exec(select(Conversation).where(Conversation.title.ilike(f"%{query}%")))).all()
        return result