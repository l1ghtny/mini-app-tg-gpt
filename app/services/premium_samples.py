from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import RequestLedger, utcnow_naive
from app.schemas.conversion import PremiumSampleStateResponse
from app.services.subscription_check.entitlements import get_current_subscription, list_text_entitlements

FLAGSHIP_TEXT_SAMPLE_KIND = "flagship_text"
FLAGSHIP_TEXT_SAMPLE_MODELS = ("gpt-5.5", "gemini-3.1-pro-preview")
_ACTIVE_SAMPLE_STATES = ("reserved", "consumed")
PREMIUM_SAMPLE_REASON_AVAILABLE = "available"
PREMIUM_SAMPLE_REASON_ALREADY_SUBSCRIBED = "already_subscribed"
PREMIUM_SAMPLE_REASON_ALREADY_HAS_ACTIVE_SUBSCRIPTION = "already_has_active_subscription"
PREMIUM_SAMPLE_REASON_ALREADY_USED_TODAY = "already_used_today"
PREMIUM_SAMPLE_REASON_ALREADY_HAS_USABLE_ACCESS = "already_has_usable_access"


def _utc_day_start(now: datetime | None = None) -> datetime:
    moment = now or utcnow_naive()
    return datetime(moment.year, moment.month, moment.day, 0, 0, 0)


def _next_utc_midnight(now: datetime | None = None) -> datetime:
    return _utc_day_start(now).replace(tzinfo=UTC) + timedelta(days=1)


def premium_sample_access_path(kind: str) -> str:
    return f"premium_sample:{kind}"


def premium_sample_models(kind: str) -> tuple[str, ...]:
    if kind == FLAGSHIP_TEXT_SAMPLE_KIND:
        return FLAGSHIP_TEXT_SAMPLE_MODELS
    raise KeyError(kind)


def _is_paid_subscription(subscription) -> bool:
    tier = getattr(subscription, "tier", None)
    if tier is None:
        return False
    return int(getattr(tier, "price_cents", 0) or 0) > 0


def _has_usable_text_access(breakdown: dict | None) -> bool:
    if not breakdown:
        return False

    selected = breakdown.get("selected")
    if selected:
        remaining = selected.get("remaining", 0)
        return remaining == -1 or remaining > 0

    total_remaining = breakdown.get("total_remaining", 0)
    return total_remaining == -1 or total_remaining > 0


async def _has_premium_sample_usage_today(
    session: AsyncSession,
    *,
    user_id,
    kind: str,
) -> bool:
    statement = (
        select(func.count())
        .where(
            RequestLedger.user_id == user_id,
            RequestLedger.feature == "text",
            RequestLedger.access_path == premium_sample_access_path(kind),
            RequestLedger.state.in_(_ACTIVE_SAMPLE_STATES),
            RequestLedger.created_at >= _utc_day_start(),
        )
    )
    used_count = (await session.exec(statement)).one() or 0
    return bool(used_count)


async def get_recent_premium_sample_kind(
    session: AsyncSession,
    *,
    user_id,
    within_days: int = 7,
) -> str | None:
    statement = (
        select(RequestLedger.access_path)
        .where(
            RequestLedger.user_id == user_id,
            RequestLedger.feature == "text",
            RequestLedger.access_path.is_not(None),
            RequestLedger.access_path.like("premium_sample:%"),
            RequestLedger.state.in_(_ACTIVE_SAMPLE_STATES),
            RequestLedger.created_at >= utcnow_naive() - timedelta(days=within_days),
        )
        .order_by(RequestLedger.created_at.desc())
        .limit(1)
    )
    access_path = (await session.exec(statement)).first()
    if not access_path:
        return None
    return str(access_path).split("premium_sample:", 1)[-1] or None


async def get_premium_sample_state(
    session: AsyncSession,
    *,
    user_id,
) -> PremiumSampleStateResponse:
    kind = FLAGSHIP_TEXT_SAMPLE_KIND
    models = list(premium_sample_models(kind))
    current_subscription = await get_current_subscription(session, user_id)
    if current_subscription is not None:
        return PremiumSampleStateResponse(
            status="ineligible",
            eligible=False,
            reason=(
                PREMIUM_SAMPLE_REASON_ALREADY_SUBSCRIBED
                if _is_paid_subscription(current_subscription)
                else PREMIUM_SAMPLE_REASON_ALREADY_HAS_ACTIVE_SUBSCRIPTION
            ),
            kinds=[],
            available_models=[],
            default_model=None,
            remaining_uses_today=0,
            next_reset_at=None,
        )

    breakdown = await list_text_entitlements(session, user_id, models[0])

    if _has_usable_text_access(breakdown):
        return PremiumSampleStateResponse(
            status="ineligible",
            eligible=False,
            reason=PREMIUM_SAMPLE_REASON_ALREADY_HAS_USABLE_ACCESS,
            kinds=[],
            available_models=[],
            default_model=None,
            remaining_uses_today=0,
            next_reset_at=None,
        )

    if await _has_premium_sample_usage_today(session, user_id=user_id, kind=kind):
        return PremiumSampleStateResponse(
            status="consumed",
            eligible=False,
            reason=PREMIUM_SAMPLE_REASON_ALREADY_USED_TODAY,
            kinds=[kind],
            available_models=models,
            default_model=models[0],
            remaining_uses_today=0,
            next_reset_at=_next_utc_midnight().isoformat(),
        )

    return PremiumSampleStateResponse(
        status="available",
        eligible=True,
        reason=PREMIUM_SAMPLE_REASON_AVAILABLE,
        kinds=[kind],
        available_models=models,
        default_model=models[0],
        remaining_uses_today=1,
        next_reset_at=_next_utc_midnight().isoformat(),
    )


async def assert_premium_sample_can_be_used(
    session: AsyncSession,
    *,
    user_id,
    kind: str,
    model: str,
) -> None:
    try:
        models = premium_sample_models(kind)
    except KeyError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_premium_sample_kind",
                "kind": kind,
            },
        ) from exc

    if model not in models:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "premium_sample_model_mismatch",
                "kind": kind,
                "requested_model": model,
                "allowed_models": list(models),
            },
        )

    current_subscription = await get_current_subscription(session, user_id)
    if current_subscription is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "premium_sample_not_applicable",
                "kind": kind,
                "reason": (
                    PREMIUM_SAMPLE_REASON_ALREADY_SUBSCRIBED
                    if _is_paid_subscription(current_subscription)
                    else PREMIUM_SAMPLE_REASON_ALREADY_HAS_ACTIVE_SUBSCRIPTION
                ),
            },
        )

    breakdown = await list_text_entitlements(session, user_id, models[0])
    if _has_usable_text_access(breakdown):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "premium_sample_not_applicable",
                "kind": kind,
                "reason": PREMIUM_SAMPLE_REASON_ALREADY_HAS_USABLE_ACCESS,
            },
        )

    if await _has_premium_sample_usage_today(session, user_id=user_id, kind=kind):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "premium_sample_already_used",
                "kind": kind,
                "next_reset_at": _next_utc_midnight().isoformat(),
            },
        )
