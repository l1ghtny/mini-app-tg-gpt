import uuid

from fastapi import HTTPException
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.subscription_tiers import UsagePack
from app.schemas.subscriptions import (
    UsagePackImageModelLimitResponse,
    UsagePackModelLimitResponse,
    UsagePackResponse,
)


def _pack_to_response(pack: UsagePack) -> UsagePackResponse:
    return UsagePackResponse(
        id=str(pack.id),
        name=pack.name,
        name_ru=pack.name_ru,
        description=pack.description,
        description_ru=pack.description_ru,
        price_cents=pack.price_cents,
        is_active=pack.is_active,
        is_public=pack.is_public,
        index=pack.index,
        model_limits=[
            UsagePackModelLimitResponse(
                model_name=l.model_name,
                request_credits=l.request_credits,
            )
            for l in pack.pack_model_limits
        ],
        image_model_limits=[
            UsagePackImageModelLimitResponse(
                image_model=l.image_model,
                credit_amount=l.credit_amount,
            )
            for l in pack.pack_image_model_limits
        ],
    )


async def list_public_packs(session: AsyncSession) -> list[UsagePackResponse]:
    packs = (await session.exec(
        select(UsagePack)
        .where(
            UsagePack.is_public == True,
            UsagePack.is_active == True,
        )
        .order_by(UsagePack.index)
        .options(
            selectinload(UsagePack.pack_model_limits),
            selectinload(UsagePack.pack_image_model_limits),
        )
    )).all()

    return [_pack_to_response(pack) for pack in packs]


async def get_pack(session: AsyncSession, pack_id: uuid.UUID) -> UsagePackResponse:
    pack = (await session.exec(
        select(UsagePack)
        .where(UsagePack.id == pack_id)
        .options(
            selectinload(UsagePack.pack_model_limits),
            selectinload(UsagePack.pack_image_model_limits),
        )
    )).first()
    if not pack or not pack.is_active:
        raise HTTPException(status_code=404, detail="Usage pack not found")

    return _pack_to_response(pack)
