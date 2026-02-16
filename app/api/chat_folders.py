import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, Response
from sqlmodel.ext.asyncio.session import AsyncSession
from pydantic import BaseModel

from app.api import chat_folder_helpers
from app.api.dependencies import get_current_user
from app.db.database import get_session
from app.db.models import AppUser
from app.schemas.chat_folders import ChatFolder, ChatFolderCreate, ChatFolderUpdate, ChatFolderWithConversations
from app.schemas.chat import ConversationAPI

router = APIRouter()

class MoveConversationRequest(BaseModel):
    folder_id: Optional[uuid.UUID] = None

@router.post("/chat-folders", response_model=ChatFolder)
async def create_folder(
    request: ChatFolderCreate,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    return await chat_folder_helpers.handle_create_folder(
        request=request,
        session=session,
        current_user=current_user,
    )

@router.get("/chat-folders", response_model=List[ChatFolder])
async def get_folders(
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    return await chat_folder_helpers.handle_get_folders(
        session=session,
        current_user=current_user,
        include_conversations=False,
    )

@router.get("/chat-folders/{folder_id}", response_model=ChatFolderWithConversations)
async def get_folder(
    folder_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    return await chat_folder_helpers.handle_get_folder(
        folder_id=folder_id,
        session=session,
        current_user=current_user,
    )

@router.put("/chat-folders/{folder_id}", response_model=ChatFolder)
async def update_folder(
    folder_id: uuid.UUID,
    request: ChatFolderUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    return await chat_folder_helpers.handle_update_folder(
        folder_id=folder_id,
        request=request,
        session=session,
        current_user=current_user,
    )

@router.delete("/chat-folders/{folder_id}", status_code=204, response_class=Response)
async def delete_folder(
    folder_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    await chat_folder_helpers.handle_delete_folder(
        folder_id=folder_id,
        session=session,
        current_user=current_user,
    )
    return Response(status_code=204)

@router.post("/conversations/{conversation_id}/move", response_model=ConversationAPI)
async def move_conversation(
    conversation_id: uuid.UUID,
    request: MoveConversationRequest,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    return await chat_folder_helpers.handle_move_conversation(
        conversation_id=conversation_id,
        folder_id=request.folder_id,
        session=session,
        current_user=current_user,
    )
