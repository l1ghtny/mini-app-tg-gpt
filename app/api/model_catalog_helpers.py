from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import ImageModelCatalog, ImageQualityPricing, TextModelCatalog
from app.schemas.models_catalog import (
    ImageModelCatalogEntryResponse,
    ImageModelQualityCatalogEntryResponse,
    ModelsCatalogResponse,
    TextModelCatalogEntryResponse,
    TextModelSupportsResponse,
    TierRequirementResponse,
)


_QUALITY_SORT_RANK = {
    "auto": 0,
    "standard": 1,
    "low": 2,
    "medium": 3,
    "high": 4,
}


def _normalize_tier_required(value: Any) -> TierRequirementResponse | None:
    if value is None:
        return None
    if isinstance(value, str):
        return TierRequirementResponse(slug=value)
    if isinstance(value, dict):
        return TierRequirementResponse(
            id=value.get("id"),
            slug=value.get("slug"),
            min_rank=value.get("min_rank"),
        )
    return None


def _normalize_supports(value: Any) -> TextModelSupportsResponse:
    if not isinstance(value, dict):
        return TextModelSupportsResponse()
    return TextModelSupportsResponse(
        vision=bool(value.get("vision", False)),
        web_search=bool(value.get("web_search", False)),
        file_search=bool(value.get("file_search", False)),
        image_gen=bool(value.get("image_gen", False)),
        reasoning=bool(value.get("reasoning", False)),
    )


def _quality_sort_key(q: ImageQualityPricing) -> tuple[int, str]:
    rank = _QUALITY_SORT_RANK.get((q.quality or "").lower(), 100)
    return rank, q.quality or ""


async def get_models_catalog(session: AsyncSession, user) -> ModelsCatalogResponse:
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    text_rows = (await session.exec(
        select(TextModelCatalog)
        .where(TextModelCatalog.is_active == True)
        .order_by(TextModelCatalog.sort_index, TextModelCatalog.model_name)
    )).all()

    image_rows = (await session.exec(
        select(ImageModelCatalog)
        .where(ImageModelCatalog.is_active == True)
        .order_by(ImageModelCatalog.sort_index, ImageModelCatalog.model_name)
    )).all()

    quality_rows = (await session.exec(
        select(ImageQualityPricing)
        .where(ImageQualityPricing.is_active == True)
        .order_by(ImageQualityPricing.image_model, ImageQualityPricing.quality)
    )).all()

    qualities_by_model: dict[str, list[ImageQualityPricing]] = {}
    for row in quality_rows:
        qualities_by_model.setdefault(row.image_model, []).append(row)

    text_models = [
        TextModelCatalogEntryResponse(
            model_name=row.model_name,
            display_name=row.display_name,
            display_name_ru=row.display_name_ru,
            provider=row.provider,
            tagline=row.tagline,
            tagline_ru=row.tagline_ru,
            description=row.description,
            description_ru=row.description_ru,
            best_for=list(row.best_for or []),
            best_for_ru=list(row.best_for_ru or []),
            not_great_for=list(row.not_great_for or []),
            not_great_for_ru=list(row.not_great_for_ru or []),
            speed=row.speed,
            intelligence=row.intelligence,
            context_window=row.context_window,
            supports=_normalize_supports(row.supports),
            tier_required=_normalize_tier_required(row.tier_required),
            badges=list(row.badges or []),
            credit_cost_hint=float(row.credit_cost_hint) if row.credit_cost_hint is not None else None,
        )
        for row in text_rows
    ]

    image_models = []
    for row in image_rows:
        model_qualities = sorted(qualities_by_model.get(row.model_name, []), key=_quality_sort_key)
        image_models.append(
            ImageModelCatalogEntryResponse(
                model_name=row.model_name,
                display_name=row.display_name,
                display_name_ru=row.display_name_ru,
                provider=row.provider,
                tagline=row.tagline,
                tagline_ru=row.tagline_ru,
                description=row.description,
                description_ru=row.description_ru,
                best_for=list(row.best_for or []),
                best_for_ru=list(row.best_for_ru or []),
                speed=row.speed,
                qualities=[
                    ImageModelQualityCatalogEntryResponse(
                        quality=q.quality,
                        credit_cost=q.credit_cost,
                        description=q.description,
                        description_ru=q.description_ru,
                    )
                    for q in model_qualities
                ],
                tier_required=_normalize_tier_required(row.tier_required),
                badges=list(row.badges or []),
            )
        )

    updated_candidates: list[datetime] = []
    updated_candidates.extend([r.updated_at for r in text_rows if r.updated_at])
    updated_candidates.extend([r.updated_at for r in image_rows if r.updated_at])
    updated_at = max(updated_candidates) if updated_candidates else datetime.now(timezone.utc).replace(tzinfo=None)

    return ModelsCatalogResponse(
        text_models=text_models,
        image_models=image_models,
        updated_at=updated_at,
    )
