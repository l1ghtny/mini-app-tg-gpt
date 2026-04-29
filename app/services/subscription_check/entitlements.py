import uuid
from datetime import datetime, timedelta, UTC
from typing import Optional

from sqlalchemy.orm import selectinload
from sqlmodel import select, func
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import RequestLedger, ImageQualityPricing, utcnow_naive
from app.db.subscription_tiers import (
    SubscriptionTier,
    TierModelLimit,
    TierImageModelLimit,
    UserSubscription,
    SubscriptionStatus,
    UsagePack,
    UsagePackModelLimit,
    UsagePackImageModelLimit,
    UserUsagePack,
    UsagePackSource,
    UsagePackStatus,
)
from app.services.subscription_check.pacing import check_image_pacing, get_image_quality_pricing


async def month_start_expr():
    return func.date_trunc("month", func.now())


async def get_current_subscription(
    session: AsyncSession,
    user_id: uuid.UUID,
) -> UserSubscription | None:
    q = (
        select(UserSubscription)
        .join(SubscriptionTier, UserSubscription.tier_id == SubscriptionTier.id)
        .where(
            UserSubscription.user_id == user_id,
            UserSubscription.status == SubscriptionStatus.active,
            (UserSubscription.expires_at.is_(None)) | (UserSubscription.expires_at > func.now()),
        )
        .order_by(
            SubscriptionTier.price_cents.desc(),
            SubscriptionTier.index.desc(),
            UserSubscription.started_at.desc(),
        )
        .limit(1)
        .options(
            selectinload(UserSubscription.tier).selectinload(SubscriptionTier.tier_model_limits)
        )
    )
    return (await session.exec(q)).first()


async def get_active_subscriptions(
    session: AsyncSession,
    user_id: uuid.UUID,
) -> list[UserSubscription]:
    q = (
        select(UserSubscription)
        .join(SubscriptionTier, UserSubscription.tier_id == SubscriptionTier.id)
        .where(
            UserSubscription.user_id == user_id,
            UserSubscription.status == SubscriptionStatus.active,
            (UserSubscription.expires_at.is_(None)) | (UserSubscription.expires_at > func.now()),
        )
        .options(
            selectinload(UserSubscription.tier)
            .selectinload(SubscriptionTier.tier_model_limits),
            selectinload(UserSubscription.tier)
            .selectinload(SubscriptionTier.tier_image_model_limits),
            selectinload(UserSubscription.tier)
            .selectinload(SubscriptionTier.tier_image_quality_limits),
        )
    )
    return (await session.exec(q)).all()


async def get_active_usage_packs(
    session: AsyncSession,
    user_id: uuid.UUID,
) -> list[UserUsagePack]:
    q = (
        select(UserUsagePack)
        .join(UsagePack, UserUsagePack.pack_id == UsagePack.id)
        .where(
            UserUsagePack.user_id == user_id,
            UserUsagePack.status == UsagePackStatus.active,
            (UserUsagePack.expires_at.is_(None)) | (UserUsagePack.expires_at > func.now()),
            UsagePack.is_active == True,
        )
        .options(
            selectinload(UserUsagePack.pack)
            .selectinload(UsagePack.pack_model_limits),
            selectinload(UserUsagePack.pack)
            .selectinload(UsagePack.pack_image_model_limits),
        )
    )
    return (await session.exec(q)).all()


async def get_active_tier(session: AsyncSession, user_id: uuid.UUID) -> SubscriptionTier | None:
    sub = await get_current_subscription(session, user_id)
    if not sub:
        return None
    return sub.tier


def _tier_usage_filter(tier_id: uuid.UUID):
    return (
        (RequestLedger.tier_id == tier_id)
        | (
            RequestLedger.tier_id.is_(None)
            & RequestLedger.usage_pack_id.is_(None)
        )
    )


def _pack_usage_filter(pack_id: uuid.UUID):
    return RequestLedger.usage_pack_id == pack_id


def _days_in_month(year: int, month: int) -> int:
    # month: 1..12
    if month == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month + 1, 1)
    this_month = datetime(year, month, 1)
    return (next_month - this_month).days


def _add_months(year: int, month: int, delta_months: int) -> tuple[int, int]:
    # returns (year, month) with month 1..12
    total = (year * 12 + (month - 1)) + delta_months
    new_year = total // 12
    new_month = (total % 12) + 1
    return new_year, new_month


def _latest_billing_boundary(now: datetime, anchor_day: int) -> datetime:
    """
    Given current time `now` and anchor day-of-month (1..31),
    returns the latest boundary datetime (00:00) not in the future.

    If anchor_day doesn't exist in a month, clamps to last day of that month.
    """
    if not (1 <= anchor_day <= 31):
        raise ValueError(f"anchor_day must be in 1..31, got {anchor_day}")

    y, m = now.year, now.month
    dim = _days_in_month(y, m)
    this_day = min(anchor_day, dim)
    this_boundary = datetime(y, m, this_day, 0, 0, 0)

    if now >= this_boundary:
        return this_boundary

    py, pm = _add_months(y, m, -1)
    pdim = _days_in_month(py, pm)
    prev_day = min(anchor_day, pdim)
    return datetime(py, pm, prev_day, 0, 0, 0)


async def usage_window_start_dt(session: AsyncSession, user_id: uuid.UUID, tier: SubscriptionTier) -> datetime:
    """
    Python-based usage window start:
    - Non-recurring tiers: since subscription started_at
    - Recurring tiers: since last billing boundary based on started_at day-of-month
    """
    sub = (await session.exec(
        select(UserSubscription)
        .where(
            UserSubscription.user_id == user_id,
            UserSubscription.tier_id == tier.id,
            UserSubscription.status == SubscriptionStatus.active
        )
        .order_by(UserSubscription.started_at.desc())
        .limit(1)
    )).first()

    # Safe fallback (shouldn't happen): calendar month start in Python
    now = utcnow_naive()
    if not sub or not sub.started_at:
        return datetime(now.year, now.month, 1, 0, 0, 0)

    if not getattr(tier, "is_recurring", True):
        return sub.started_at

    anchor_day = sub.started_at.day
    return _latest_billing_boundary(now=now, anchor_day=anchor_day)


async def get_usage_start_date(session: AsyncSession, user_id: uuid.UUID, tier: SubscriptionTier) -> datetime:
    """
    Deprecated wrapper: kept for compatibility.
    Prefer usage_window_start_dt().
    """
    return await usage_window_start_dt(session, user_id, tier)


async def remaining_requests_for_model(session: AsyncSession, user_id: uuid.UUID, tier_id: uuid.UUID,
                                       model_name: str) -> int:
    tier = await session.get(SubscriptionTier, tier_id)
    if not tier:
        return 0

    cap_row = (await session.exec(
        select(TierModelLimit.monthly_requests).where(
            TierModelLimit.tier_id == tier_id,
            TierModelLimit.model_name == model_name
        ).limit(1)
    )).first()

    cap = cap_row or 0

    if cap == -1:
        return -1

    if cap == 0:
        return 0

    # Python window start (no SQL CASE/make_date/interval)
    start_dt = await usage_window_start_dt(session, user_id, tier)

    used = (await session.exec(
        select(func.count())
        .where(
            RequestLedger.user_id == user_id,
            _tier_usage_filter(tier_id),
            RequestLedger.model_name == model_name,
            RequestLedger.feature == "text",
            RequestLedger.state.in_(("reserved", "consumed")),
            RequestLedger.created_at >= start_dt
        )
    )).one()

    return max(0, cap - (used or 0))


async def remaining_image_requests_for_model(
    session: AsyncSession,
    user_id: uuid.UUID,
    tier_id: uuid.UUID,
    image_model: str,
) -> float:
    tier = await session.get(SubscriptionTier, tier_id)
    if not tier:
        return 0

    cap_row = (await session.exec(
        select(TierImageModelLimit.monthly_requests).where(
            TierImageModelLimit.tier_id == tier_id,
            TierImageModelLimit.image_model == image_model,
        ).limit(1)
    )).first()

    cap = cap_row or 0

    if cap == -1:
        return -1

    if cap == 0:
        return 0

    start_dt = await usage_window_start_dt(session, user_id, tier)

    used = (await session.exec(
        select(func.coalesce(func.sum(RequestLedger.cost), 0))
        .where(
            RequestLedger.user_id == user_id,
            _tier_usage_filter(tier_id),
            RequestLedger.model_name == image_model,
            RequestLedger.feature == "image",
            RequestLedger.state.in_(("reserved", "consumed")),
            RequestLedger.created_at >= start_dt,
        )
    )).one()

    return max(0, cap - (used or 0))


async def remaining_pack_requests_for_model(
    session: AsyncSession,
    pack: UserUsagePack,
    model_name: str,
) -> int:
    limit = next((l for l in pack.pack.pack_model_limits if l.model_name == model_name), None)
    if not limit:
        return 0

    cap = limit.request_credits or 0
    if cap == -1:
        return -1
    if cap == 0:
        return 0

    used = (await session.exec(
        select(func.count())
        .where(
            RequestLedger.user_id == pack.user_id,
            _pack_usage_filter(pack.id),
            RequestLedger.model_name == model_name,
            RequestLedger.feature == "text",
            RequestLedger.state.in_(("reserved", "consumed")),
        )
    )).one()

    return max(0, cap - (used or 0))


async def remaining_pack_image_requests_for_model(
    session: AsyncSession,
    pack: UserUsagePack,
    image_model: str,
) -> float:
    limit = next((l for l in pack.pack.pack_image_model_limits if l.image_model == image_model), None)
    if not limit:
        return 0

    cap = limit.credit_amount or 0
    if cap == -1:
        return -1
    if cap == 0:
        return 0

    used = (await session.exec(
        select(func.coalesce(func.sum(RequestLedger.cost), 0))
        .where(
            RequestLedger.user_id == pack.user_id,
            _pack_usage_filter(pack.id),
            RequestLedger.model_name == image_model,
            RequestLedger.feature == "image",
            RequestLedger.state.in_(("reserved", "consumed")),
        )
    )).one()

    return max(0, cap - (used or 0))


async def remaining_images(session: AsyncSession, user_id: uuid.UUID, tier: SubscriptionTier) -> int:
    cap = tier.monthly_images or 0
    if cap == 0:
        return 0

    start_dt = await usage_window_start_dt(session, user_id, tier)

    used_total = (await session.exec(
        select(func.coalesce(func.sum(RequestLedger.cost), 0))
        .where(
            RequestLedger.user_id == user_id,
            _tier_usage_filter(tier.id),
            RequestLedger.feature == "image",
            RequestLedger.state.in_(("reserved", "consumed")),
            RequestLedger.created_at >= start_dt
        )
    )).one()

    return max(0, cap - int(used_total))


def _tier_usage_source(tier: SubscriptionTier) -> str:
    if tier.price_cents > 0 and getattr(tier, "is_recurring", True):
        return "subscription"
    if tier.price_cents > 0:
        return "paid"
    return "free"


def _sort_subscriptions(subs: list[UserSubscription]) -> list[UserSubscription]:
    def sort_key(sub: UserSubscription) -> tuple[int, int, int, datetime]:
        tier = sub.tier
        if tier.price_cents > 0 and getattr(tier, "is_recurring", True):
            source_rank = 3
        elif tier.price_cents > 0:
            source_rank = 2
        else:
            source_rank = 1
        return (
            source_rank,
            tier.price_cents,
            tier.index or 0,
            sub.started_at or datetime.min,
        )

    return sorted(subs, key=sort_key, reverse=True)


def _sort_usage_packs(packs: list[UserUsagePack]) -> list[UserUsagePack]:
    def sort_key(pack: UserUsagePack) -> tuple[int, datetime, datetime]:
        source_rank = 2 if pack.source == UsagePackSource.paid else 1
        expires_at = pack.expires_at or datetime.max
        purchased_at = pack.purchased_at or datetime.min
        return (-source_rank, expires_at, purchased_at)

    # paid packs first, then earliest expiry
    return sorted(packs, key=sort_key)


async def get_tier_usage_counts(
    session: AsyncSession,
    user_id: uuid.UUID,
    tier: SubscriptionTier,
    model_names: list[str]
) -> dict[str, int]:
    if not model_names:
        return {}

    start_dt = await usage_window_start_dt(session, user_id, tier)

    statement = (
        select(RequestLedger.model_name, func.count())
        .where(
            RequestLedger.user_id == user_id,
            _tier_usage_filter(tier.id),
            RequestLedger.feature == "text",
            RequestLedger.state.in_(("reserved", "consumed")),
            RequestLedger.created_at >= start_dt,
            RequestLedger.model_name.in_(model_names)
        )
        .group_by(RequestLedger.model_name)
    )

    results = (await session.exec(statement)).all()
    return {r[0]: r[1] for r in results}


async def get_pack_usage_counts(
    session: AsyncSession,
    user_id: uuid.UUID,
    pack_ids: list[uuid.UUID],
    model_names: list[str]
) -> dict[tuple[uuid.UUID, str], int]:
    if not pack_ids or not model_names:
        return {}

    statement = (
        select(RequestLedger.usage_pack_id, RequestLedger.model_name, func.count())
        .where(
            RequestLedger.user_id == user_id,
            RequestLedger.usage_pack_id.in_(pack_ids),
            RequestLedger.feature == "text",
            RequestLedger.state.in_(("reserved", "consumed")),
            RequestLedger.model_name.in_(model_names)
        )
        .group_by(RequestLedger.usage_pack_id, RequestLedger.model_name)
    )

    results = (await session.exec(statement)).all()
    # Map (pack_id, model_name) -> count
    return {(r[0], r[1]): r[2] for r in results}


async def get_tier_image_usage_sums(
    session: AsyncSession,
    user_id: uuid.UUID,
    tier: SubscriptionTier,
    image_models: list[str]
) -> dict[str, float]:
    if not image_models:
        return {}

    start_dt = await usage_window_start_dt(session, user_id, tier)

    statement = (
        select(RequestLedger.model_name, func.coalesce(func.sum(RequestLedger.cost), 0))
        .where(
            RequestLedger.user_id == user_id,
            _tier_usage_filter(tier.id),
            RequestLedger.feature == "image",
            RequestLedger.state.in_(("reserved", "consumed")),
            RequestLedger.created_at >= start_dt,
            RequestLedger.model_name.in_(image_models)
        )
        .group_by(RequestLedger.model_name)
    )

    results = (await session.exec(statement)).all()
    return {r[0]: r[1] for r in results}


async def get_pack_image_usage_sums(
    session: AsyncSession,
    user_id: uuid.UUID,
    pack_ids: list[uuid.UUID],
    image_models: list[str]
) -> dict[tuple[uuid.UUID, str], float]:
    if not pack_ids or not image_models:
        return {}

    statement = (
        select(RequestLedger.usage_pack_id, RequestLedger.model_name, func.coalesce(func.sum(RequestLedger.cost), 0))
        .where(
            RequestLedger.user_id == user_id,
            RequestLedger.usage_pack_id.in_(pack_ids),
            RequestLedger.feature == "image",
            RequestLedger.state.in_(("reserved", "consumed")),
            RequestLedger.model_name.in_(image_models)
        )
        .group_by(RequestLedger.usage_pack_id, RequestLedger.model_name)
    )

    results = (await session.exec(statement)).all()
    return {(r[0], r[1]): r[2] for r in results}


async def get_tier_image_usage_total(
    session: AsyncSession,
    user_id: uuid.UUID,
    tier: SubscriptionTier,
    image_models: list[str],
) -> float:
    if not image_models:
        return 0.0

    start_dt = await usage_window_start_dt(session, user_id, tier)
    used_total = (await session.exec(
        select(func.coalesce(func.sum(RequestLedger.cost), 0))
        .where(
            RequestLedger.user_id == user_id,
            _tier_usage_filter(tier.id),
            RequestLedger.feature == "image",
            RequestLedger.state.in_(("reserved", "consumed")),
            RequestLedger.created_at >= start_dt,
            RequestLedger.model_name.in_(image_models),
        )
    )).one()
    return used_total or 0.0


async def get_pack_image_usage_total(
    session: AsyncSession,
    user_id: uuid.UUID,
    pack_id: uuid.UUID,
    image_models: list[str],
) -> float:
    if not image_models:
        return 0.0

    used_total = (await session.exec(
        select(func.coalesce(func.sum(RequestLedger.cost), 0))
        .where(
            RequestLedger.user_id == user_id,
            RequestLedger.usage_pack_id == pack_id,
            RequestLedger.feature == "image",
            RequestLedger.state.in_(("reserved", "consumed")),
            RequestLedger.model_name.in_(image_models),
        )
    )).one()
    return used_total or 0.0


async def list_text_entitlements_bulk(
    session: AsyncSession,
    user_id: uuid.UUID,
    model_names: Optional[list[str]] = None,
    subscriptions: Optional[list[UserSubscription]] = None,
    usage_packs: Optional[list[UserUsagePack]] = None,
) -> dict[str, dict]:
    # 1. Get active subs and packs
    subs = subscriptions if subscriptions is not None else await get_active_subscriptions(session, user_id)
    packs = usage_packs if usage_packs is not None else await get_active_usage_packs(session, user_id)

    # 2. Determine models if not provided
    if model_names is None:
        model_names_set = set()
        for sub in subs:
            for limit in sub.tier.tier_model_limits:
                model_names_set.add(limit.model_name)
        for pack in packs:
            for limit in pack.pack.pack_model_limits:
                model_names_set.add(limit.model_name)
        model_names = list(model_names_set)

    if not model_names:
        return {}

    # 3. Fetch usage counts
    # For tiers
    tier_usages = {}  # tier_id -> {model_name -> count}
    for sub in subs:
        tier_usages[sub.tier.id] = await get_tier_usage_counts(session, user_id, sub.tier, model_names)

    # For packs
    pack_ids = [p.id for p in packs]
    pack_usage_map = await get_pack_usage_counts(session, user_id, pack_ids, model_names)

    # 4. Build result
    result = {}

    sorted_subs = _sort_subscriptions(subs)
    sorted_packs = _sort_usage_packs(packs)

    for model_name in model_names:
        tier_entries = []
        for sub in sorted_subs:
            tier = sub.tier
            limit = next((l for l in tier.tier_model_limits if l.model_name == model_name), None)
            if not limit:
                continue

            used = tier_usages.get(tier.id, {}).get(model_name, 0)
            cap = limit.monthly_requests or 0

            if cap == -1:
                remaining = -1
            elif cap == 0:
                remaining = 0
            else:
                remaining = max(0, cap - used)

            tier_entries.append({
                "kind": "tier",
                "source": _tier_usage_source(tier),
                "tier_id": str(tier.id),
                "usage_pack_id": None,
                "pack_id": None,
                "name": tier.name,
                "cap": cap,
                "used": used,
                "remaining": remaining,
                "expires_at": None,
                "purchased_at": None,
            })

        pack_entries = []
        for pack in sorted_packs:
            limit = next((l for l in pack.pack.pack_model_limits if l.model_name == model_name), None)
            if not limit:
                continue

            used = pack_usage_map.get((pack.id, model_name), 0)
            cap = limit.request_credits or 0

            if cap == -1:
                remaining = -1
            elif cap == 0:
                remaining = 0
            else:
                remaining = max(0, cap - used)

            pack_entries.append({
                "kind": "pack",
                "source": pack.source.value,
                "tier_id": None,
                "usage_pack_id": str(pack.id),
                "pack_id": str(pack.pack_id),
                "name": pack.pack.name,
                "cap": cap,
                "used": used,
                "remaining": remaining,
                "expires_at": pack.expires_at,
                "purchased_at": pack.purchased_at,
            })

        entitlements = tier_entries + pack_entries
        selected = next((e for e in tier_entries if e["remaining"] > 0 or e["remaining"] == -1), None)
        if not selected:
            selected = next((e for e in pack_entries if e["remaining"] > 0 or e["remaining"] == -1), None)

        total_remaining = sum(e["remaining"] for e in entitlements if e["remaining"] != -1)

        result[model_name] = {
            "entitlements": entitlements,
            "selected": selected,
            "total_remaining": total_remaining,
        }

    return result


async def list_text_entitlements(
    session: AsyncSession,
    user_id: uuid.UUID,
    model_name: str,
) -> dict:
    # Use bulk implementation for single model to avoid code duplication
    bulk_result = await list_text_entitlements_bulk(session, user_id, [model_name])
    return bulk_result.get(model_name, {
        "entitlements": [],
        "selected": None,
        "total_remaining": 0,
    })


async def list_image_entitlements_bulk(
    session: AsyncSession,
    user_id: uuid.UUID,
    image_models: Optional[list[str]] = None,
    subscriptions: Optional[list[UserSubscription]] = None,
    usage_packs: Optional[list[UserUsagePack]] = None,
) -> dict[str, dict]:
    # 1. Get active subs and packs
    subs = subscriptions if subscriptions is not None else await get_active_subscriptions(session, user_id)
    packs = usage_packs if usage_packs is not None else await get_active_usage_packs(session, user_id)

    # 2. Determine models if not provided
    if image_models is None:
        image_models_set = set()
        for sub in subs:
            for limit in sub.tier.tier_image_model_limits:
                image_models_set.add(limit.image_model)
        for pack in packs:
            for limit in pack.pack.pack_image_model_limits:
                image_models_set.add(limit.image_model)
        image_models = list(image_models_set)

    if not image_models:
        return {}

    # 3. Fetch shared usage sums (across allowed image models per source)
    tier_usages = {}
    tier_allowed_models: dict[uuid.UUID, list[str]] = {}
    for sub in subs:
        allowed = sorted({l.image_model for l in sub.tier.tier_image_model_limits})
        tier_allowed_models[sub.tier.id] = allowed
        tier_usages[sub.tier.id] = await get_tier_image_usage_total(
            session,
            user_id,
            sub.tier,
            allowed,
        )

    pack_usages: dict[uuid.UUID, float] = {}
    pack_allowed_models: dict[uuid.UUID, list[str]] = {}
    for pack in packs:
        allowed = sorted({l.image_model for l in pack.pack.pack_image_model_limits})
        pack_allowed_models[pack.id] = allowed
        pack_usages[pack.id] = await get_pack_image_usage_total(
            session,
            user_id,
            pack.id,
            allowed,
        )

    # 4. Build result
    result = {}
    sorted_subs = _sort_subscriptions(subs)
    sorted_packs = _sort_usage_packs(packs)

    for image_model in image_models:
        tier_entries = []
        for sub in sorted_subs:
            tier = sub.tier
            limit = next((l for l in tier.tier_image_model_limits if l.image_model == image_model), None)
            if not limit:
                continue

            used = float(tier_usages.get(tier.id, 0.0))
            cap = limit.monthly_requests or 0
            daily_image_limit = tier.daily_image_limit or 0

            if daily_image_limit > 0:
                cap = -1
                remaining = -1
            elif cap == -1:
                remaining = -1
            elif cap == 0:
                remaining = 0
            else:
                remaining = max(0, cap - used)

            source = _tier_usage_source(tier)
            pacing = None
            allowed_models = sorted({l.image_model for l in tier.tier_image_model_limits})
            allowed_qualities = sorted({l.quality for l in tier.tier_image_quality_limits})

            tier_entries.append({
                "kind": "tier",
                "source": source,
                "tier_id": str(tier.id),
                "usage_pack_id": None,
                "pack_id": None,
                "name": tier.name,
                "cap": cap,
                "used": used,
                "remaining_credits": remaining,
                "pacing": pacing,
                "daily_image_limit": daily_image_limit,
                "allowed_image_qualities": allowed_qualities,
                "allowed_image_models": allowed_models,
                "expires_at": None,
                "purchased_at": None,
            })

        pack_entries = []
        for pack in sorted_packs:
            limit = next((l for l in pack.pack.pack_image_model_limits if l.image_model == image_model), None)
            if not limit:
                continue

            used = float(pack_usages.get(pack.id, 0.0))
            cap = limit.credit_amount or 0

            if cap == -1:
                remaining = -1
            elif cap == 0:
                remaining = 0
            else:
                remaining = max(0, cap - used)

            pack_entries.append({
                "kind": "pack",
                "source": pack.source.value,
                "tier_id": None,
                "usage_pack_id": str(pack.id),
                "pack_id": str(pack.pack_id),
                "name": pack.pack.name,
                "cap": cap,
                "used": used,
                "remaining_credits": remaining,
                "expires_at": pack.expires_at,
                "purchased_at": pack.purchased_at,
                "daily_image_limit": None,
            })

        entitlements = tier_entries + pack_entries
        if any(e["remaining_credits"] == -1 for e in entitlements):
            total_remaining_credits = -1
        else:
            total_remaining_credits = sum(e["remaining_credits"] for e in entitlements)

        result[image_model] = {
            "entitlements": entitlements,
            "total_remaining_credits": total_remaining_credits,
        }

    return result


async def list_image_entitlements(
    session: AsyncSession,
    user_id: uuid.UUID,
    image_model: str,
) -> dict:
    bulk_result = await list_image_entitlements_bulk(session, user_id, [image_model])
    return bulk_result.get(image_model, {
        "entitlements": [],
        "total_remaining_credits": 0,
    })


async def select_text_entitlement(
    session: AsyncSession,
    user_id: uuid.UUID,
    model_name: str,
) -> dict:
    breakdown = await list_text_entitlements(session, user_id, model_name)
    selected = breakdown["selected"]
    if selected:
        return selected

    return {
        "kind": "none",
        "source": "none",
        "tier_id": None,
        "usage_pack_id": None,
        "cap": 0,
        "used": 0,
        "remaining": 0,
    }


async def select_image_entitlement(
    session: AsyncSession,
    user_id: uuid.UUID,
    image_model: str,
    quality: str,
) -> dict:
    pricing = await get_image_quality_pricing(session, image_model, quality)
    if not pricing:
        return {
            "kind": "none",
            "source": "none",
            "tier_id": None,
            "usage_pack_id": None,
            "cap": 0,
            "used": 0,
            "remaining_credits": 0,
            "cost": 0,
            "allowed": False,
            "throttle_reason": "unavailable",
            "wait_time": None,
        }
    cost = pricing.credit_cost or 1.0
    breakdown = await list_image_entitlements(session, user_id, image_model)
    entitlements = breakdown["entitlements"]
    tier_entries = [e for e in entitlements if e["kind"] == "tier"]
    pack_entries = [e for e in entitlements if e["kind"] == "pack"]

    throttled_waits: list[timedelta] = []
    model_allowed = bool(pack_entries)
    quality_allowed = bool(pack_entries)

    eligible_tier_entries = []
    for ent in tier_entries:
        allowed_models = ent.get("allowed_image_models") or []
        if image_model not in allowed_models:
            continue
        model_allowed = True

        allowed_qualities = ent.get("allowed_image_qualities") or []
        if quality and quality not in allowed_qualities:
            continue
        quality_allowed = True
        eligible_tier_entries.append(ent)

    daily_tier_entries = [e for e in eligible_tier_entries if (e.get("daily_image_limit") or 0) > 0]
    monthly_tier_entries = [e for e in eligible_tier_entries if (e.get("daily_image_limit") or 0) <= 0]

    def _allow(ent: dict) -> dict:
        selected = ent.copy()
        selected["cost"] = cost
        selected["allowed"] = True
        selected["throttle_reason"] = None
        selected["wait_time"] = None
        return selected

    def _has_sufficient_credits(ent: dict) -> bool:
        remaining = ent.get("remaining_credits", 0)
        return remaining == -1 or remaining >= cost

    # Daily tiers are prioritized universally (free and paid).
    for ent in daily_tier_entries:
        if not _has_sufficient_credits(ent):
            continue
        tier_id = uuid.UUID(ent["tier_id"]) if ent.get("tier_id") else None
        if not tier_id:
            continue
        daily_target = ent.get("daily_image_limit") or 0
        is_throttled, wait_time = await check_image_pacing(
            session,
            user_id,
            daily_target=daily_target,
            cost=cost,
            tier_id=tier_id,
        )
        if is_throttled:
            throttled_waits.append(wait_time)
            continue
        return _allow(ent)

    # Monthly-only tiers use the old monthly window flow.
    for ent in monthly_tier_entries:
        if not _has_sufficient_credits(ent):
            continue
        return _allow(ent)

    # Packs are always monthly credit pools and are checked after tiers.
    for ent in pack_entries:
        if not _has_sufficient_credits(ent):
            continue
        return _allow(ent)

    if not model_allowed:
        return {
            "kind": "none",
            "source": "none",
            "tier_id": None,
            "usage_pack_id": None,
            "cap": 0,
            "used": 0,
            "remaining_credits": 0,
            "cost": cost,
            "allowed": False,
            "throttle_reason": "model_restricted",
            "wait_time": None,
        }

    if model_allowed and not quality_allowed:
        return {
            "kind": "none",
            "source": "none",
            "tier_id": None,
            "usage_pack_id": None,
            "cap": 0,
            "used": 0,
            "remaining_credits": 0,
            "cost": cost,
            "allowed": False,
            "throttle_reason": "quality_restricted",
            "wait_time": None,
        }

    if throttled_waits:
        wait_time = min(throttled_waits)
        return {
            "kind": "none",
            "source": "none",
            "tier_id": None,
            "usage_pack_id": None,
            "cap": 0,
            "used": 0,
            "remaining_credits": 0,
            "cost": cost,
            "allowed": False,
            "throttle_reason": "pacing",
            "wait_time": wait_time,
        }

    return {
        "kind": "none",
        "source": "none",
        "tier_id": None,
        "usage_pack_id": None,
        "cap": 0,
        "used": 0,
        "remaining_credits": 0,
        "cost": cost,
        "allowed": False,
        "throttle_reason": "quota",
        "wait_time": None,
    }


# requests in real time


async def reserve_request(session, *, user_id, conversation_id, assistant_message_id,
                          request_id, model_name, feature, cost, tool_choice=None, tier_id=None,
                          usage_pack_id=None):

    # try insert; on duplicate (same request_id), just return the existing row

    rl = RequestLedger(user_id=user_id, tier_id=tier_id, usage_pack_id=usage_pack_id, conversation_id=conversation_id,
                       assistant_message_id=assistant_message_id,
                       request_id=request_id, model_name=model_name,
                       feature=feature, tool_choice=tool_choice, state="reserved", cost=cost)
    session.add(rl)
    try:
        await session.commit()
        await session.refresh(rl)
        return rl
    except Exception:
        await session.rollback()
        # fetch existing
        rl = (await session.exec(
            select(RequestLedger).where(RequestLedger.user_id==user_id,
                                        RequestLedger.request_id==request_id)
        )).first()
        return rl


async def finalize_request(session, *, request_id, user_id, success: bool):
    rl = (await session.exec(
        select(RequestLedger).where(RequestLedger.user_id==user_id, RequestLedger.request_id==request_id)
    )).first()
    if rl:
        rl.state = "consumed" if success else "refunded"
        await session.commit()


async def get_daily_text_count(session: AsyncSession, user_id: uuid.UUID, model: str) -> int:
    """
    Counts how many text messages were sent using a specific model in the last 24h.
    """
    start_window = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)

    statement = select(func.count()).where(
        RequestLedger.user_id == user_id,
        RequestLedger.model_name == model,
        RequestLedger.feature == "text",
        RequestLedger.created_at >= start_window
    )

    result = await session.exec(statement)
    return result.first()


async def get_daily_usage_cost(session: AsyncSession, user_id: uuid.UUID, feature: str) -> int:
    # 1. Define Window: Now minus 24 hours
    window_start = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=24)

    # 2. Sum the 'cost' column
    statement = select(func.sum(RequestLedger.cost)).where(
        RequestLedger.user_id == user_id,
        RequestLedger.feature == feature,
        RequestLedger.state.in_(("reserved", "consumed")),
        RequestLedger.created_at >= window_start
    )

    result = await session.exec(statement)
    total_cost = result.first()

    # Handle None (if no rows found)
    return total_cost if total_cost is not None else 0
