import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Header, Request, Response
from redis.asyncio import Redis
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse
from sqlmodel import select, desc
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.responses import RedirectResponse

from app.api.dependencies import get_current_user, get_bus, get_redis, rate_limit_check, get_available_models
from app.api.helpers import generate_and_publish, load_conversation
from app.core.metrics import track_event
from app.db import models
from app.db.database import get_session
from app.db.models import AppUser, Conversation, Message, RequestLedger
from app.redis.event_bus import RedisEventBus
from app.schemas.chat import ConversationAPI, ConversationWithMessages, NewMessageRequest, RenameRequest, \
    UpdateConversationSettingsRequest, MessageCreated, RequestExists
from app.services.background.image_deriver import ensure_openai_compatible_image_url, rewrite_message_image_url
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

router = APIRouter()


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
    conversation = await load_conversation(session, conversation_id)
    if conversation.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to send messages to this conversation")
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
    allowed = image_entitlement["source"] != "none"
    tier_id = uuid.UUID(image_entitlement["tier_id"]) if image_entitlement.get("tier_id") else None
    usage_pack_id = (
        uuid.UUID(image_entitlement["usage_pack_id"])
        if image_entitlement.get("usage_pack_id")
        else None
    )
    cost = pricing.credit_cost or 1.0
    return ImageEntitlementSelection(
        allowed=allowed,
        tier_id=tier_id,
        usage_pack_id=usage_pack_id,
        cost=cost,
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


def _apply_image_quota_notice(system_prompt: str | None, image_allowed: bool) -> str:
    prompt = system_prompt or ""
    if image_allowed:
        return prompt

    return (
        prompt
        + "\n\nSYSTEM NOTICE: The user has used up their image generation quota. "
        "The image generation tool has been disabled. "
        "If the user wants you to generate an image, you have to explicitly tell them they have reached their image limit "
        "and need to buy more usage or wait for their quota to refill. "
        "Tell the user to click on their profile in the sidebar menu and purchase additional usage packs. "
        "Don't suggest prompts for usage in other apps.\n\n"
    )


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
        .order_by(Message.created_at.asc())
    )
    msgs = result.all()
    for msg in msgs:
        await session.refresh(msg, attribute_names=["content"])
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
    tool_choice: str | None,
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


@router.post("/conversations/{conversation_id}/messages", status_code=202, response_model=MessageCreated | RequestExists)
async def create_message(
        conversation_id: uuid.UUID,
        request: NewMessageRequest,
        background_tasks: BackgroundTasks,
        session: AsyncSession = Depends(get_session),
        current_user: AppUser = Depends(get_current_user),
        bus: Redis = Depends(get_redis),
        _rate_limit_ok: bool = Depends(rate_limit_check),
):
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

    text_entitlement = await _require_text_entitlement(session, current_user, request.model)
    await _enforce_gpt52_safeguard(session, current_user, request.model)
    conversation = await _load_conversation_for_user(session, conversation_id, current_user.id)

    image_model, image_quality = _resolve_image_settings(request, conversation)
    image_entitlement = await _require_image_entitlement(
        session,
        current_user.id,
        image_model,
        image_quality,
    )
    tools = await _build_tools(
        image_entitlement.allowed,
        image_model,
        image_quality,
    )

    if request.tool_choice == "image_generation" and not image_entitlement.allowed:
        raise HTTPException(status_code=402, detail="image_quota_exceeded")

    system_prompt = _apply_image_quota_notice(conversation.system_prompt, image_entitlement.allowed)

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
        tool_choice=request.tool_choice,
        tier_id=text_entitlement.tier_id,
        usage_pack_id=text_entitlement.usage_pack_id,
    )

    history_for_openai = await _build_history_for_openai(session, conversation_id)
    redis_bus = await get_bus(bus)

    _queue_generation(
        background_tasks,
        conversation_id=conversation_id,
        assistant_message_id=assistant_msg.id,
        user_id=current_user.id,
        history_for_openai=history_for_openai,
        bus=redis_bus,
        instructions=system_prompt,
        model=request.model,
        tool_choice=request.tool_choice,
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


@router.get(
    "/conversations/{conversation_id}/messages/{message_id}/stream",
    response_class=EventSourceResponse,
)
async def stream_message(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    request: Request,
    bus: RedisEventBus = Depends(get_bus),  # your DI
    current_user=Depends(get_current_user),
    last_event_id: str | None = Header(None, convert_underscores=False, alias="Last-Event-ID"),
    session: AsyncSession = Depends(get_session),
):
    await _load_conversation_for_user(session, conversation_id, current_user.id)

    start_id = last_event_id or "0-0"

    async def gen():
        # If stream missing (expired) you can synthesize from DB here if you want.
        async for sid, ev in bus.read(str(message_id), start_id):
            if await request.is_disconnected():
                return
            # Send the exact type + JSON you stored
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
        "X-Accel-Buffering": "no",  # for nginx
    }
    return EventSourceResponse(gen(), headers=headers)


@router.post("/conversations", response_model=ConversationAPI)
async def create_conversation(session: AsyncSession = Depends(get_session), current_user: AppUser = Depends(get_current_user)):
    """
    Creates a new conversation for the user.
    """
    # First, ensure the temporary user exists
    user = await session.get(models.AppUser, current_user.id)
    if not user:
        user = models.AppUser(id=current_user.id, telegram_id=current_user.telegram_id)  # Example telegram_id
        session.add(user)
        await session.commit()
        await session.refresh(user)

    new_conversation = models.Conversation(title="New Chat", user_id=user.id)
    session.add(new_conversation)
    await session.commit()
    await session.refresh(new_conversation)
    return new_conversation


@router.get("/conversations", response_model=List[Conversation])
async def get_conversations(session: AsyncSession = Depends(get_session), current_user: AppUser = Depends(get_current_user)):
    """
    Gets all conversations for the user.
    """
    query = (
        select(models.Conversation)
        .where(models.Conversation.user_id == current_user.id)
        .order_by(
            desc(func.coalesce(models.Conversation.updated_at)).nulls_last()
        )
    )

    conversations = await session.exec(query)
    return conversations.all()


@router.get("/conversations/{conversation_id}/messages", response_model=ConversationWithMessages)
async def get_conversation_messages(conversation_id: uuid.UUID, session: AsyncSession = Depends(get_session), current_user: AppUser = Depends(get_current_user)):
    """
    Gets a specific conversation and all its messages.
    """

    query = select(Conversation).where(Conversation.id == conversation_id).options(selectinload(Conversation.messages).selectinload(models.Message.content))
    conversation = await session.exec(query)
    conversation = conversation.first()

    if not conversation or conversation.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    conversation.messages.sort(key=lambda m: (m.created_at is None, m.created_at))

    return conversation


@router.get("/conversations/{cid}/stream", response_class=Response)
async def sse_conversation(cid: uuid.UUID, request: Request,
                           r: Redis = Depends(get_redis), current_user: AppUser = Depends(get_current_user)):
    mid = await r.get(f"conv:{cid}:current")
    if not mid:
        # Nothing active; you can 204, or synthesize from DB (like above), or 404.
        # I’d recommend 204 No Content.
        return Response(status_code=204)
    # 307 preserves method and lets client follow transparently
    return RedirectResponse(url=f"/api/v1/conversations/{cid}/messages/{mid}/stream", status_code=307)



@router.put("/conversations/{conversation_id}", response_model=Conversation)
async def rename_conversation(
    conversation_id: uuid.UUID,
    request: RenameRequest,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user)
):
    """
    Renames a specific conversation.
    """
    conversation = await session.get(models.Conversation, conversation_id)
    if not conversation or conversation.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    conversation.title = request.title
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return conversation


@router.delete("/conversations/{conversation_id}", status_code=204, response_class=Response)
async def delete_conversation(
    conversation_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user)
):
    """
    Deletes a specific conversation and all its messages.
    """
    conversation = await session.get(models.Conversation, conversation_id)
    if not conversation or conversation.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # SQLModel will handle cascading deletes for messages if the relationship is set up correctly
    await session.delete(conversation)
    await session.commit()
    return


@router.put("/conversations/{conversation_id}/settings", response_model=ConversationAPI)
async def update_conversation_settings(
        conversation_id: uuid.UUID,
        request: UpdateConversationSettingsRequest,
        session: AsyncSession = Depends(get_session),
        current_user: models.AppUser = Depends(get_current_user)  # Secure the endpoint
):
    """
    Updates the settings (like system prompt) for a specific conversation.
    """
    conversation = await session.get(models.Conversation, conversation_id)
    if not conversation or conversation.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Update fields if they were provided in the request
    if request.system_prompt is not None:
        conversation.system_prompt = request.system_prompt

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
