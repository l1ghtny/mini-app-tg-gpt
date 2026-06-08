import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Row
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import ImageQualityPricing, RequestLedger
from app.services.model_registry import get_image_model_provider


@dataclass(frozen=True)
class ImageEnergySnapshot:
    daily_target: float
    max_burst_days: int
    capacity: float
    available_energy: float
    saved_energy: float
    used_energy: float
    refill_rate_per_sec: float
    is_throttled: bool
    wait_time: timedelta
    as_of: datetime


async def get_image_energy_snapshot(
    session: AsyncSession,
    user_id: uuid.UUID,
    daily_target: float,
    max_burst_days: int = 5,
    cost: float = 0.0,
    tier_id: uuid.UUID | None = None,
    is_recurring: bool = True,
    total_pool: float = 0.0,
) -> ImageEnergySnapshot:
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if is_recurring:
        refill_rate_per_sec = daily_target / 86400.0
        capacity = daily_target * max_burst_days
    else:
        refill_rate_per_sec = daily_target / 86400.0
        capacity = total_pool

    if capacity <= 0 and refill_rate_per_sec <= 0:
        return ImageEnergySnapshot(
            daily_target=0.0,
            max_burst_days=max_burst_days,
            capacity=0.0,
            available_energy=0.0,
            saved_energy=0.0,
            used_energy=0.0,
            refill_rate_per_sec=0.0,
            is_throttled=cost > 0,
            wait_time=timedelta(seconds=0),
            as_of=now,
        )

    start_window = now - timedelta(days=30)
    if not is_recurring:
        # For non-recurring pools, we look back as far as needed to count all usage since sub start
        # but 90 days is a safe practical limit for the "lifetime" check if we don't have sub start here
        start_window = now - timedelta(days=90)

    query = (
        select(RequestLedger)
        .where(
            RequestLedger.user_id == user_id,
            RequestLedger.feature == "image",
            RequestLedger.state.in_(("reserved", "consumed")),
            RequestLedger.created_at >= start_window,
        )
        .order_by(RequestLedger.created_at.asc())
    )
    if tier_id is not None:
        query = query.where(
            (RequestLedger.tier_id == tier_id)
            | ((RequestLedger.tier_id.is_(None)) & (RequestLedger.usage_pack_id.is_(None)))
        )
    history = (await session.exec(query)).all()

    current_tokens = capacity
    last_time = start_window
    for request in history:
        req_time = request.created_at
        elapsed = (req_time - last_time).total_seconds()
        current_tokens = min(capacity, current_tokens + (elapsed * refill_rate_per_sec))

        req_cost = request.cost if request.cost else 1.0
        current_tokens -= req_cost
        last_time = req_time

    elapsed_since_last = (now - last_time).total_seconds()
    current_tokens = min(capacity, current_tokens + (elapsed_since_last * refill_rate_per_sec))

    available_energy = max(0.0, current_tokens)
    saved_energy = max(0.0, min(capacity, available_energy) - daily_target) if is_recurring else 0.0
    used_energy = max(0.0, capacity - min(capacity, available_energy))

    if cost <= 0 or available_energy >= cost:
        is_throttled = False
        wait_time = timedelta(seconds=0)
    else:
        missing = cost - available_energy
        if refill_rate_per_sec > 0:
            is_throttled = True
            wait_time = timedelta(seconds=(missing / refill_rate_per_sec))
        else:
            is_throttled = True
            wait_time = timedelta(days=365)  # Effectively infinite if no refill

    return ImageEnergySnapshot(
        daily_target=daily_target,
        max_burst_days=max_burst_days,
        capacity=capacity,
        available_energy=available_energy,
        saved_energy=saved_energy,
        used_energy=used_energy,
        refill_rate_per_sec=refill_rate_per_sec,
        is_throttled=is_throttled,
        wait_time=wait_time,
        as_of=now,
    )


async def check_image_pacing(
    session: AsyncSession,
    user_id: uuid.UUID,
    daily_target: float = 4.0,
    max_burst_days: int = 5,
    cost: float = 1.0,
    tier_id: uuid.UUID | None = None,
    is_recurring: bool = True,
    total_pool: float = 0.0,
) -> tuple[bool, timedelta]:
    snapshot = await get_image_energy_snapshot(
        session=session,
        user_id=user_id,
        daily_target=daily_target,
        max_burst_days=max_burst_days,
        cost=cost,
        tier_id=tier_id,
        is_recurring=is_recurring,
        total_pool=total_pool,
    )
    return snapshot.is_throttled, snapshot.wait_time


async def get_image_quality_pricing(
    session: AsyncSession,
    image_model: str,
    quality_name: str,
) -> Row[Any] | None | Any:
    try:
        provider = get_image_model_provider(image_model)
    except KeyError:
        provider = None
    if provider == "google" and quality_name not in {"512", "1k", "2k"}:
        return None
    statement = select(ImageQualityPricing).where(
        ImageQualityPricing.image_model == image_model,
        ImageQualityPricing.quality == quality_name,
        ImageQualityPricing.is_active == True,
    )
    result = await session.exec(statement)
    return result.first()


async def get_image_quality_cost(session: AsyncSession, image_model: str, quality_name: str) -> float:
    pricing = await get_image_quality_pricing(session, image_model, quality_name)
    return pricing.credit_cost if pricing else 1.0
