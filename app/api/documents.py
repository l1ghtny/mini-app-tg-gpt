import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Response, UploadFile
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api import document_helpers
from app.api.dependencies import get_current_user
from app.db.database import get_session
from app.db.models import AppUser
from app.schemas.documents import DocumentCapabilitiesResponse, DocumentsListResponse, UserDocumentResponse

documents = APIRouter(tags=["documents"], prefix="/documents")


@documents.post("/upload", response_model=UserDocumentResponse)
async def upload_document(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    provider_override: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    return await document_helpers.upload_document(
        session=session,
        user=current_user,
        background_tasks=background_tasks,
        upload=file,
        provider_override=provider_override,
    )


@documents.get("", response_model=DocumentsListResponse)
async def get_documents(
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    return await document_helpers.list_documents(session, current_user)


@documents.get("/capabilities", response_model=DocumentCapabilitiesResponse)
async def get_document_capabilities(
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    return await document_helpers.get_document_capabilities(session, current_user)


@documents.delete("/{document_id}", status_code=204, response_class=Response)
async def delete_document(
    document_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    await document_helpers.delete_document(
        session=session,
        user=current_user,
        document_id=document_id,
        background_tasks=background_tasks,
    )
    return Response(status_code=204)


@documents.post("/{document_id}/pin", response_model=UserDocumentResponse)
async def pin_document(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    return await document_helpers.set_document_pin_state(
        session=session,
        user=current_user,
        document_id=document_id,
        pin=True,
    )


@documents.post("/{document_id}/unpin", response_model=UserDocumentResponse)
async def unpin_document(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: AppUser = Depends(get_current_user),
):
    return await document_helpers.set_document_pin_state(
        session=session,
        user=current_user,
        document_id=document_id,
        pin=False,
    )
