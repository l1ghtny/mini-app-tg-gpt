from fastapi import APIRouter, Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api import model_catalog_helpers
from app.api.dependencies import get_current_user
from app.db.database import get_session
from app.schemas.models_catalog import ModelsCatalogResponse

models_catalog = APIRouter(tags=["models"], prefix="/models")


@models_catalog.get("/catalog", response_model=ModelsCatalogResponse)
async def get_models_catalog(
    user=Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await model_catalog_helpers.get_models_catalog(session, user)
