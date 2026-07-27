import uuid
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy import delete
from sqlmodel import select, desc
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import AppUser, ChatFolder, ChatFolderDocument, Conversation, UserDocument
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
    await session.flush()
    await _replace_folder_documents(session, new_folder, request.document_ids, current_user.id)
    await session.commit()
    return await _load_owned_folder(session, new_folder.id, current_user.id)

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
        query = query.options(
            selectinload(ChatFolder.conversations),
            selectinload(ChatFolder.attached_documents),
        )
    else:
        query = query.options(selectinload(ChatFolder.attached_documents))

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
        .options(
            selectinload(ChatFolder.conversations),
            selectinload(ChatFolder.attached_documents),
        )
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
    if request.document_ids is not None:
        await _replace_folder_documents(session, folder, request.document_ids, current_user.id)

    session.add(folder)
    await session.commit()
    return await _load_owned_folder(session, folder.id, current_user.id)

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


async def _load_owned_folder(
    session: AsyncSession,
    folder_id: uuid.UUID,
    user_id: uuid.UUID,
) -> ChatFolder:
    folder = (await session.exec(
        select(ChatFolder)
        .where(ChatFolder.id == folder_id, ChatFolder.user_id == user_id)
        .options(selectinload(ChatFolder.attached_documents))
        .execution_options(populate_existing=True)
    )).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
    return folder


async def _replace_folder_documents(
    session: AsyncSession,
    folder: ChatFolder,
    document_ids: list[uuid.UUID],
    user_id: uuid.UUID,
) -> None:
    normalized_ids = list(dict.fromkeys(document_ids))
    if normalized_ids:
        owned_ids = set((await session.exec(
            select(UserDocument.id).where(
                UserDocument.id.in_(normalized_ids),
                UserDocument.user_id == user_id,
                UserDocument.deleted_at.is_(None),
                UserDocument.status == "ready",
            )
        )).all())
        missing = [str(document_id) for document_id in normalized_ids if document_id not in owned_ids]
        if missing:
            raise HTTPException(
                status_code=400,
                detail={"error": "project_documents_not_ready_or_not_owned", "document_ids": missing},
            )

    await session.exec(delete(ChatFolderDocument).where(ChatFolderDocument.folder_id == folder.id))
    for document_id in normalized_ids:
        session.add(ChatFolderDocument(folder_id=folder.id, document_id=document_id))
