from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Response
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api import model_catalog_helpers, tier_helpers, usage_pack_helpers
from app.db.database import get_session
from app.schemas.public_catalog import PublicProductCatalogResponse

public_catalog = APIRouter(tags=["public product catalog"], prefix="/public")


@public_catalog.get("/catalog", response_model=PublicProductCatalogResponse)
async def get_public_product_catalog(
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> PublicProductCatalogResponse:
    response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=3600"
    return PublicProductCatalogResponse(
        generated_at=datetime.now(UTC),
        models=await model_catalog_helpers.get_models_catalog(session),
        tiers=await tier_helpers.list_public_tiers_for_catalog(session),
        usage_packs=await usage_pack_helpers.list_public_packs(session),
    )
