import uuid
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request, Response
from redis.asyncio import Redis
from sse_starlette.sse import EventSourceResponse
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api import chat_helpers
from app.api.dependencies import get_bus, get_current_user, get_redis, rate_limit_check
from app.db.models import AppUser, Conversation
from app.db.database import get_session
from app.redis.event_bus import RedisEventBus
from app.schemas.chat import (
    CreateConversationRequest,
    ConversationAPI,
    ConversationWithMessages,
    EditMessageRequest,
    MessageUpdated,
    MessageCreated,
    NewMessageRequest,
    RenameRequest,
    RequestExists,
    UpdateConversationSettingsRequest, ConversationInfo,
)

router = APIRouter()


@router.post(
    "/conversations/{conversation_id}/messages",
    status_code=202,
    response_model=MessageCreated | RequestExists,
)
async def create_message(
    conversation_id: uuid.UUID,
    request: NewMessageRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
    bus: Redis = Depends(get_redis),
    _rate_limit_ok: bool = Depends(rate_limit_check),
):
    return await chat_helpers.handle_create_message(
        conversation_id=conversation_id,
        request=request,
        background_tasks=background_tasks,
        session=session,
        current_user=current_user,
        bus=bus,
    )


@router.delete(
    "/conversations/{conversation_id}/messages/{message_id}",
    status_code=204,
    response_class=Response,
)
async def delete_message(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    await chat_helpers.handle_delete_message(
        conversation_id=conversation_id,
        message_id=message_id,
        session=session,
        current_user=current_user,
    )
    return Response(status_code=204)


@router.put(
    "/conversations/{conversation_id}/messages/{message_id}",
    response_model=MessageUpdated,
)
async def edit_message(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    request: EditMessageRequest,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    return await chat_helpers.handle_edit_message(
        conversation_id=conversation_id,
        message_id=message_id,
        request=request,
        session=session,
        current_user=current_user,
    )


@router.get(
    "/conversations/{conversation_id}/messages/{message_id}/stream",
    response_class=EventSourceResponse,
)
async def stream_message(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    request: Request,
    bus: RedisEventBus = Depends(get_bus),
    current_user: AppUser = Depends(get_current_user),
    last_event_id: str | None = Header(None, convert_underscores=False, alias="Last-Event-ID"),
    session: AsyncSession = Depends(get_session),
):
    return await chat_helpers.handle_stream_message(
        conversation_id=conversation_id,
        message_id=message_id,
        request=request,
        bus=bus,
        current_user=current_user,
        last_event_id=last_event_id,
        session=session,
    )


@router.post("/conversations", response_model=ConversationAPI)
async def create_conversation(
    request: CreateConversationRequest | None = None,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    return await chat_helpers.handle_create_conversation(
        session=session,
        current_user=current_user,
        folder_id=request.folder_id if request else None,
    )


@router.get("/conversations", response_model=List[Conversation])
async def get_conversations(
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    return await chat_helpers.handle_get_conversations(
        session=session,
        current_user=current_user,
    )


@router.get("/conversations/{conversation_id}/messages", response_model=ConversationWithMessages)
async def get_conversation_messages(
    conversation_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    return await chat_helpers.handle_get_conversation_messages(
        conversation_id=conversation_id,
        session=session,
        current_user=current_user,
    )


@router.get("/conversations/{cid}/stream", response_class=Response)
async def sse_conversation(
    cid: uuid.UUID,
    request: Request,
    r: Redis = Depends(get_redis),
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    return await chat_helpers.handle_sse_conversation(
        conversation_id=cid,
        redis=r,
        session=session,
        current_user=current_user,
    )


@router.put("/conversations/{conversation_id}", response_model=Conversation)
async def rename_conversation(
    conversation_id: uuid.UUID,
    request: RenameRequest,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    return await chat_helpers.handle_rename_conversation(
        conversation_id=conversation_id,
        request=request,
        session=session,
        current_user=current_user,
    )


@router.delete("/conversations/{conversation_id}", status_code=204, response_class=Response)
async def delete_conversation(
    conversation_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    await chat_helpers.handle_delete_conversation(
        conversation_id=conversation_id,
        session=session,
        current_user=current_user,
    )
    return Response(status_code=204)


@router.put("/conversations/{conversation_id}/settings", response_model=ConversationAPI)
async def update_conversation_settings(
    conversation_id: uuid.UUID,
    request: UpdateConversationSettingsRequest,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    return await chat_helpers.handle_update_conversation_settings(
        conversation_id=conversation_id,
        request=request,
        session=session,
        current_user=current_user,
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationInfo)
async def get_conversation(conversation_id: uuid.UUID, session: AsyncSession = Depends(get_session), current_user: AppUser = Depends(get_current_user)):
    return await chat_helpers.handle_get_conversation(
        conversation_id=conversation_id,
        session=session,
        current_user=current_user,
    )


@router.get("/conversations/search/{string}")
async def search_conversations(string: str, session: AsyncSession = Depends(get_session), current_user: AppUser = Depends(get_current_user)):
    return await chat_helpers.handle_conversation_search(
        query=string,
        session=session,
        current_user=current_user,
    )
