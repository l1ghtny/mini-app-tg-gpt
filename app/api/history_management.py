"""Explicit, owner-scoped history selection and transactional bulk deletion."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Uuid, column, or_, table
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.dependencies import get_current_user
from app.db.database import get_session
from app.db.models import (
    AppUser,
    ChatFolder,
    Conversation,
    Message,
    MessageContent,
    MessageActivityEvent,
    RequestLedger,
    State,
)

router = APIRouter(prefix="/history", tags=["history"])

# Work's schema is deployed on both channels, while its ORM/runtime is beta-only.
work_run = table(
    "work_run",
    column("conversation_id", Uuid),
    column("folder_id", Uuid),
    column("user_id", Uuid),
    column("status"),
)
TERMINAL_WORK_STATUSES = ("succeeded", "failed", "cancelled", "refunded")


class ChatSelection(BaseModel):
    id: uuid.UUID
    folder_id: uuid.UUID | None = None


class ProjectSelection(BaseModel):
    id: uuid.UUID
    conversation_ids: list[uuid.UUID] = Field(max_length=10000)
    document_ids: list[uuid.UUID] = Field(max_length=10000)


class BulkDeleteRequest(BaseModel):
    conversations: list[ChatSelection] = Field(default_factory=list, max_length=10000)
    projects: list[ProjectSelection] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def require_selection(self):
        if not self.conversations and not self.projects:
            raise ValueError("Select at least one chat or project")
        for items in (self.conversations, self.projects):
            if len({item.id for item in items}) != len(items):
                raise ValueError("Duplicate selections are not allowed")
        return self


class ManagedChat(ChatSelection):
    title: str
    is_favorite: bool
    busy: bool


class ManagedProject(ProjectSelection):
    name: str
    busy: bool


class ManagedHistory(BaseModel):
    conversations: list[ManagedChat]
    projects: list[ManagedProject]


class BulkDeleteResult(BaseModel):
    deleted_conversation_ids: list[uuid.UUID]
    deleted_project_ids: list[uuid.UUID]
    skipped_conversation_ids: list[uuid.UUID]
    skipped_project_ids: list[uuid.UUID]


async def busy_history(session: AsyncSession, user_id: uuid.UUID):
    chats = set(
        (
            await session.exec(
                select(RequestLedger.conversation_id).where(
                    RequestLedger.user_id == user_id,
                    RequestLedger.state == State.reserved,
                    RequestLedger.conversation_id.is_not(None),
                )
            )
        ).all()
    )
    # A text ledger can finish before image/tool output. Persisted activity is
    # shared by beta and production, unlike their separate Redis instances.
    chats.update(
        (
            await session.exec(
                select(Message.conversation_id)
                .join(
                    MessageActivityEvent,
                    MessageActivityEvent.message_id == Message.id,
                )
                .join(Conversation, Conversation.id == Message.conversation_id)
                .where(
                    Conversation.user_id == user_id,
                    MessageActivityEvent.status == "active",
                )
            )
        ).all()
    )
    work = (
        await session.exec(
            select(
                work_run.c.conversation_id,
                work_run.c.folder_id,
            ).where(
                work_run.c.user_id == user_id,
                work_run.c.status.not_in(TERMINAL_WORK_STATUSES),
            )
        )
    ).all()
    chats.update(row.conversation_id for row in work if row.conversation_id)
    return chats, {row.folder_id for row in work if row.folder_id}


@router.get("/manage", response_model=ManagedHistory)
async def get_managed_history(
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    chats = (
        await session.exec(
            select(Conversation)
            .where(
                Conversation.user_id == current_user.id,
            )
            .order_by(Conversation.updated_at.desc(), Conversation.id)
        )
    ).all()
    projects = (
        await session.exec(
            select(ChatFolder)
            .where(
                ChatFolder.user_id == current_user.id,
            )
            .options(selectinload(ChatFolder.attached_documents))
            .order_by(ChatFolder.id.desc())
        )
    ).all()
    busy_chats, busy_projects = await busy_history(session, current_user.id)
    return ManagedHistory(
        conversations=[
            ManagedChat(
                id=c.id,
                title=c.title,
                folder_id=c.folder_id,
                is_favorite=c.is_favorite,
                busy=c.id in busy_chats,
            )
            for c in chats
        ],
        projects=[
            ManagedProject(
                id=p.id,
                name=p.name,
                document_ids=p.document_ids,
                conversation_ids=[c.id for c in chats if c.folder_id == p.id],
                busy=p.id in busy_projects
                or any(c.id in busy_chats for c in chats if c.folder_id == p.id),
            )
            for p in projects
        ],
    )


@router.post("/bulk-delete", response_model=BulkDeleteResult)
async def bulk_delete_history(
    request: BulkDeleteRequest,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    # Folder locks prevent new membership links while the snapshot is checked.
    project_ids = {p.id for p in request.projects}
    chat_ids = {c.id for c in request.conversations}
    projects = (
        await session.exec(
            select(ChatFolder)
            .where(
                ChatFolder.id.in_(project_ids),
            )
            .order_by(ChatFolder.id)
            .with_for_update()
            .options(
                selectinload(ChatFolder.attached_documents),
            )
        )
    ).all()
    chats = (
        await session.exec(
            select(Conversation)
            .where(
                or_(
                    Conversation.id.in_(chat_ids),
                    Conversation.folder_id.in_(project_ids),
                )
            )
            .order_by(Conversation.id)
            .with_for_update()
        )
    ).all()
    if any(item.user_id != current_user.id for item in [*projects, *chats]):
        raise HTTPException(404, "Selection not found")

    projects_by_id = {p.id: p for p in projects}
    chats_by_id = {c.id: c for c in chats}
    for selected in request.conversations:
        current = chats_by_id.get(selected.id)
        if current and current.folder_id != selected.folder_id:
            raise HTTPException(409, detail={"error": "history_selection_changed"})
    for selected in request.projects:
        current = projects_by_id.get(selected.id)
        if current and (
            set(selected.conversation_ids)
            != {c.id for c in chats if c.folder_id == selected.id}
            or set(selected.document_ids) != set(current.document_ids)
        ):
            raise HTTPException(409, detail={"error": "history_selection_changed"})

    busy_chats, busy_projects = await busy_history(session, current_user.id)
    skipped_projects = project_ids & busy_projects
    skipped_projects.update(
        c.folder_id for c in chats if c.id in busy_chats and c.folder_id in project_ids
    )
    skipped_chats = {
        c.id for c in chats if c.id in busy_chats or c.folder_id in skipped_projects
    }
    deleted_chats = [c for c in chats if c.id not in skipped_chats]

    # Reuse single-chat asset semantics; library documents and accounting survive.
    from app.services.image_assets import (
        detach_assets_from_conversation_ids,
        detach_assets_from_message_content_ids,
    )

    if deleted_chats:
        ids = [c.id for c in deleted_chats]
        content_ids = (
            await session.exec(
                select(MessageContent.id)
                .join(
                    Message,
                    Message.id == MessageContent.message_id,
                )
                .where(
                    Message.conversation_id.in_(ids), MessageContent.type == "image_url"
                )
            )
        ).all()
        await detach_assets_from_message_content_ids(session, content_ids)
        await detach_assets_from_conversation_ids(session, ids)
        await session.flush()
        for chat in deleted_chats:
            await session.delete(chat)
        await session.flush()
    for project in projects:
        if project.id not in skipped_projects:
            # Children were explicitly removed above; do not run a second cascade.
            await session.delete(project)
    await session.commit()

    return BulkDeleteResult(
        # Missing explicit IDs are already deleted, making safe retries idempotent.
        deleted_conversation_ids=sorted(
            {c.id for c in deleted_chats} | (chat_ids - chats_by_id.keys())
        ),
        deleted_project_ids=sorted(project_ids - skipped_projects),
        skipped_conversation_ids=sorted(skipped_chats),
        skipped_project_ids=sorted(skipped_projects),
    )
