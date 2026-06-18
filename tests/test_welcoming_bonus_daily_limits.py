import uuid

from app.api import access_code_helpers, tier_helpers
from app.db.subscription_tiers import (
    SubscriptionTier,
    TierImageModelLimit,
    TierImageQualityLimit,
    TierModelLimit,
)


def _build_welcoming_bonus_tier() -> SubscriptionTier:
    tier_id = uuid.uuid4()
    tier = SubscriptionTier(
        id=tier_id,
        name="Welcoming Bonus",
        name_ru="Приветственный бонус",
        description="Starter tier",
        description_ru="Стартовый тариф",
        price_cents=0,
        monthly_images=80,
        daily_image_energy=80,
        monthly_docs=0,
        monthly_deepsearch=0,
        is_active=True,
        is_public=False,
        index=0,
        is_recurring=True,
    )
    tier.tier_model_limits = [
        TierModelLimit(tier_id=tier_id, model_name="gpt-5.4-nano", monthly_requests=15, daily_requests=15),
        TierModelLimit(tier_id=tier_id, model_name="gemini-3.1-flash-lite", monthly_requests=15, daily_requests=15),
        TierModelLimit(tier_id=tier_id, model_name="gpt-5.5", monthly_requests=0, daily_requests=0),
    ]
    tier.tier_image_model_limits = [
        TierImageModelLimit(tier_id=tier_id, image_model="gpt-image-1.5", monthly_requests=40),
        TierImageModelLimit(tier_id=tier_id, image_model="gpt-image-2", monthly_requests=40),
    ]
    tier.tier_image_quality_limits = [
        TierImageQualityLimit(tier_id=tier_id, quality="low"),
        TierImageQualityLimit(tier_id=tier_id, quality="medium"),
        TierImageQualityLimit(tier_id=tier_id, quality="high"),
    ]
    return tier


def test_public_tier_response_exposes_daily_welcoming_bonus_limits():
    tier = _build_welcoming_bonus_tier()

    response = tier_helpers._build_tier_response(tier, pricing_by_model={})

    fast_limit = next(limit for limit in response.tier_model_limits if limit.model_name == "gpt-5.4-nano")
    assert fast_limit.requests_limit == 15
    assert fast_limit.daily_requests_limit == 15
    assert response.daily_image_energy == 80
    assert response.image_energy_max == 400
    assert all(limit.requests_limit == -1 for limit in response.tier_image_model_limits)


def test_access_code_tier_response_matches_daily_welcoming_bonus_limits():
    tier = _build_welcoming_bonus_tier()

    response = access_code_helpers._build_tier_response(tier)

    fast_limit = next(limit for limit in response.tier_model_limits if limit.model_name == "gpt-5.4-nano")
    assert fast_limit.requests_limit == 15
    assert fast_limit.daily_requests_limit == 15
    assert response.daily_image_energy == 80
    assert response.image_energy_max == 400
