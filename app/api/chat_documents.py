import uuid
from fastapi import APIRouter, Depends, Response
from sqlmodel.ext.asyncio.session import AsyncSession
from app.api.dependencies import get_current_user
from app.db.database import get_session
from app.db.models import AppUser
from app.r2.private_documents import presign_document_source
from app.services.chat_document_renderer import MIME_TYPES
from app.services.chat_documents import owned_document, now

router = APIRouter(prefix="/chat-documents", tags=["chat-documents"])


@router.post("/{document_id}/download")
async def download_document(document_id: uuid.UUID, response: Response,
    current_user: AppUser = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    row = await owned_document(session, current_user.id, document_id)
    response.headers["Cache-Control"] = "private, no-store"
    url = await presign_document_source(bucket=row.bucket, key=row.key,
        filename=row.filename, content_type=MIME_TYPES[row.format], expires=max(1, min(300, int((row.expires_at-now()).total_seconds()))),
        disposition="attachment")
    return {"url": url, "filename": row.filename}
