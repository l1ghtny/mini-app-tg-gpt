from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api import access_code_helpers
from app.api.dependencies import get_current_user
from app.core.config import settings
from app.db.database import get_session
from app.schemas.codes import (
    AccessCodeAdminResponse,
    AccessCodeCreate,
    AccessCodeResponse,
    AccessCodeRedeemResponse,
)

access_codes = APIRouter(tags=["access codes"], prefix="/access_codes")


@access_codes.get("/{code}", response_model=AccessCodeResponse)
async def get_access_code(
    code: str,
    session: AsyncSession = Depends(get_session),
):
    access_code = await access_code_helpers.fetch_access_code_by_code(session, code)
    access_code_helpers.ensure_access_code_valid(access_code)
    return access_code_helpers.build_access_code_response(access_code)


@access_codes.post("/{code_id}/redeem", status_code=202, response_model=AccessCodeRedeemResponse)
async def redeem_access_code(
    code_id: str,
    session: AsyncSession = Depends(get_session),
    user=Depends(get_current_user),
):
    access_code = await access_code_helpers.fetch_access_code_by_id_for_update(session, code_id)
    access_code_helpers.ensure_access_code_valid(access_code)
    return await access_code_helpers.redeem_access_code_for_user(session, user, access_code)


@access_codes.post("/admin/create", response_model=AccessCodeAdminResponse)
async def create_access_code(
    payload: AccessCodeCreate,
    session: AsyncSession = Depends(get_session),
    user=Depends(get_current_user),
):
    if settings.ENVIRONMENT != "local":
        raise HTTPException(status_code=403, detail="Not allowed in production")

    return await access_code_helpers.create_access_code(session, payload)
