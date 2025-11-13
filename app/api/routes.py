import json
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Header, Request, Response
from pydantic import RootModel
from redis.asyncio import Redis
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.responses import RedirectResponse

from app.api.dependencies import get_current_user, get_bus, get_redis
from app.api.helpers import generate_and_publish, load_conversation, fetch_assistant_text
from app.db import models
from app.db.database import get_session
from app.db.models import AppUser, Conversation, Message, RequestLedger
from app.redis.event_bus import RedisEventBus
from app.schemas.chat import ConversationAPI, ConversationWithMessages, NewMessageRequest, RenameRequest, \
    UpdateConversationSettingsRequest, MessageCreated, RequestExists
from app.services.background.image_deriver import ensure_openai_compatible_image_url, rewrite_message_image_url
from app.services.streaming.test_idempotency import _choose_link_for_message
from app.services.subscription_check.entitlements import remaining_requests_for_model, remaining_images, reserve_request
from app.services.subscription_check.realtime_check import check_tier, create_tools_list
from app.services.tasks import generate_and_save_title

router = APIRouter()


@router.post("/conversations/{conversation_id}/messages", status_code=202, response_model=MessageCreated | RequestExists)
async def create_message(
        conversation_id: uuid.UUID,
        request: NewMessageRequest,
        background_tasks: BackgroundTasks,
        session: AsyncSession = Depends(get_session),
        current_user: AppUser = Depends(get_current_user),
        bus: RedisEventBus = Depends(get_bus),
):

    ## -- idempotency check --

    idempotency_key = request.client_request_id

    existing = await session.exec(
        select(RequestLedger).where(
            RequestLedger.user_id == current_user.id,
            RequestLedger.request_id == idempotency_key,
            RequestLedger.feature == "text"
        )
    )
    rl = existing.first()
    if rl and rl.assistant_message_id:
        link = await _choose_link_for_message(session, bus, conversation_id, rl.assistant_message_id, rl.created_at)
        if link["stream_url"]:
            return RequestExists(
                message_id=link["message_id"],
                stream_url=link["stream_url"]
            )
        else:
            return RequestExists(
                message_id=link["message_id"],
                messages_url=link["messages_url"]
            )

    # subscription check
    tier = await check_tier(current_user, session)
    if not tier:
        raise HTTPException(status_code=402, detail="No active subscription")

    # --- model and tools checks ---
    # 1. requests for a model
    remaining = await remaining_requests_for_model(session, current_user.id, tier.id, request.model)
    if remaining <= 0:
        # Suggest alternatives (models in allowlist with >0)
        # Optional: compute available models by iterating tier.allowed_models and checking remaining for each
        raise HTTPException(status_code=409, detail={
            "error": "model_quota_exceeded",
            "requested_model": request.model,
            "available_models": tier.allowed_models  # refine to only those with remaining > 0
        })

    # 2. images
    images_remaining = await remaining_images(session, current_user.id, tier)
    image_allowed = True if images_remaining > 0 else False

    # 3. tools
    tools = await create_tools_list(image_allowed)

    # 4. required tools

    if "image_generation" == request.tool_choice and not image_allowed:
        raise HTTPException(status_code=402, detail="image_quota_exceeded")




    # 1) Ensure the conversation exists & belongs to the user (add your auth checks)
    conversation = await load_conversation(session, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to send messages to this conversation")

    system_prompt = conversation.system_prompt

    # 2) Create a USER message and contents
    user_msg = models.Message(conversation_id=conversation_id, role="user")
    session.add(user_msg)
    await session.flush()

    for part in request.content:
        mc = models.MessageContent(message_id=user_msg.id, type=part.type, value=part.value)
        session.add(mc)
        if part.type == "text":
            background_tasks.add_task(generate_and_save_title, conversation_id, part.value)

    # Optional: update model used for conv
    if getattr(conversation, "model", None) != request.model:
        conversation.model = request.model
        session.add(conversation)

    await session.commit()
    await session.refresh(user_msg)

    # 3) Create *assistant* a placeholder message to attach final content
    assistant_msg = models.Message(conversation_id=conversation_id, role="assistant")
    session.add(assistant_msg)
    await session.commit()
    await session.refresh(assistant_msg)


    # 3.5 create the request ledger

    await reserve_request(session,
                          user_id=current_user.id, conversation_id=conversation_id,
                          assistant_message_id=assistant_msg.id,
                          request_id=idempotency_key, model_name=request.model, feature="text",
                          tool_choice=request.tool_choice)

    # 4) Build history for OpenAI
    history_for_openai = []
    # Pull last N messages (optional optimisation); so far we fetch all:
    result = await session.exec(select(Message).where(Message.conversation_id == conversation_id))
    msgs = result.all()
    for msg in msgs:
        # lazy-load content
        await session.refresh(msg, attribute_names=["content"])
        parts = []
        for c in msg.content:
            if c.type == "text" and msg.role == "user":
                parts.append({"type": "input_text", "text": c.value})
            elif c.type == "text" and msg.role == "assistant":
                parts.append({"type": "output_text", "text": c.value})
            elif c.type == "image" or "image_url" and msg.role == "user":
                compatible_url = await ensure_openai_compatible_image_url(session, c.value, max_side=2048)
                parts.append({"type": "input_image", "image_url": compatible_url})
                if compatible_url != c.value:
                    # rewrite the image URL in the DB
                    await rewrite_message_image_url(session, c.value, compatible_url, message_id=msg.id)
        history_for_openai.append({"role": msg.role, "content": parts})

    # 5) Kick off a background producer that streams to Redis and batches DB writes
    background_tasks.add_task(
        generate_and_publish,
        conversation_id=conversation_id,
        assistant_message_id=assistant_msg.id,
        user_id=current_user.id,
        history_for_openai=history_for_openai,
        bus=bus,
        instructions=system_prompt,
        model=request.model,
        tool_choice=request.tool_choice,
        tools=tools,
        request_id=idempotency_key
    )

    return {
        "message_id": str(assistant_msg.id),
        "stream_url": f"/api/v1/conversations/{conversation_id}/messages/{assistant_msg.id}/stream"
    }


bus_event = RootModel(dict)


@router.get("/conversations/{conversation_id}/messages/{message_id}/stream")
async def stream_message(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    request: Request,
    bus: RedisEventBus = Depends(get_bus),  # your DI
    current_user=Depends(get_current_user),
    last_event_id: str | None = Header(None, convert_underscores=False, alias="Last-Event-ID"),
    session: AsyncSession = Depends(get_session),
):
    conversation = await load_conversation(session, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to stream messages from this conversation")


    start_id = last_event_id or "0-0"

    async def gen():
        # If stream missing (expired) you can synthesize from DB here if you want.
        async for sid, ev in bus.read(str(message_id), start_id):
            if await request.is_disconnected():
                return
            # Send the exact type + JSON you stored
            print(ev)
            print('\n')
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
    conversations = await session.exec(
        select(models.Conversation).where(models.Conversation.user_id == current_user.id))
    conversations = conversations.all()
    return conversations


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
    return conversation


@router.get("/conversations/{cid}/stream")
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


@router.delete("/conversations/{conversation_id}", status_code=204)
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

    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return conversation