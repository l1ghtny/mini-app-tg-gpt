import uuid
from typing import List, Optional

from fastapi import HTTPException
from sqlmodel import select, desc
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import AppUser, ChatFolder, Conversation
from app.schemas.chat_folders import ChatFolderCreate, ChatFolderUpdate

async def handle_create_folder(
    *,
    request: ChatFolderCreate,
    session: AsyncSession,
    current_user: AppUser,
) -> ChatFolder:
    new_folder = ChatFolder(
        user_id=current_user.id,
        name=request.name,
        prompt=request.prompt
    )
    session.add(new_folder)
    await session.commit()
    await session.refresh(new_folder)
    return new_folder

async def handle_get_folders(
    *,
    session: AsyncSession,
    current_user: AppUser,
    include_conversations: bool = False,
) -> List[ChatFolder]:
    query = (
        select(ChatFolder)
        .where(ChatFolder.user_id == current_user.id)
        .order_by(desc(ChatFolder.id))
    )
    if include_conversations:
        query = query.options(selectinload(ChatFolder.conversations))

    result = await session.exec(query)
    folders = result.all()

    if include_conversations:
        # Sort conversations inside each folder
        # Sort by updated_at descending. Put None at the end.
        for folder in folders:
            folder.conversations.sort(
                key=lambda c: (c.updated_at is not None, c.updated_at),
                reverse=True
            )

    return folders

async def handle_get_folder(
    *,
    folder_id: uuid.UUID,
    session: AsyncSession,
    current_user: AppUser,
) -> ChatFolder:
    query = (
        select(ChatFolder)
        .where(ChatFolder.id == folder_id, ChatFolder.user_id == current_user.id)
        .options(selectinload(ChatFolder.conversations))
    )
    folder = (await session.exec(query)).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    folder.conversations.sort(
        key=lambda c: (c.updated_at is not None, c.updated_at),
        reverse=True
    )
    return folder

async def handle_update_folder(
    *,
    folder_id: uuid.UUID,
    request: ChatFolderUpdate,
    session: AsyncSession,
    current_user: AppUser,
) -> ChatFolder:
    folder = await session.get(ChatFolder, folder_id)
    if not folder or folder.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Folder not found")

    if request.name is not None:
        folder.name = request.name
    if request.prompt is not None:
        folder.prompt = request.prompt

    session.add(folder)
    await session.commit()
    await session.refresh(folder)
    return folder

async def handle_delete_folder(
    *,
    folder_id: uuid.UUID,
    session: AsyncSession,
    current_user: AppUser,
) -> None:
    folder = await session.get(ChatFolder, folder_id)
    if not folder or folder.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Folder not found")

    await session.delete(folder)
    await session.commit()

async def handle_move_conversation(
    *,
    conversation_id: uuid.UUID,
    folder_id: Optional[uuid.UUID],
    session: AsyncSession,
    current_user: AppUser,
) -> Conversation:
    conversation = await session.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if folder_id:
        folder = await session.get(ChatFolder, folder_id)
        if not folder or folder.user_id != current_user.id:
            raise HTTPException(status_code=404, detail="Folder not found")

    conversation.folder_id = folder_id
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return conversation


async def handle_folder_search(query: str, session: AsyncSession):
    async with session:
        result = (await session.exec(select(ChatFolder).where(ChatFolder.name.ilike(f"%{query}%")))).all()
        return result


