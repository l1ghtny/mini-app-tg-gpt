import uuid

from app.api import tier_helpers
from app.db.subscription_tiers import (
    SubscriptionTier,
    TierImageModelLimit,
    TierImageQualityLimit,
    TierModelLimit,
)
from app.db.models import ImageQualityPricing


def test_tier_response_uses_infinite_image_limits_when_daily_energy_present():
    tier_id = uuid.uuid4()
    tier = SubscriptionTier(
        id=tier_id,
        name="daily-tier",
        name_ru="daily-tier-ru",
        description="d",
        description_ru="d",
        price_cents=100,
        monthly_images=100,
        daily_image_energy=25,
        monthly_docs=0,
        monthly_deepsearch=0,
        is_active=True,
        is_public=True,
        index=1,
        is_recurring=True,
    )
    tier.tier_model_limits = [
        TierModelLimit(tier_id=tier_id, model_name="gpt-5.4-nano", monthly_requests=1000),
    ]
    tier.tier_image_model_limits = [
        TierImageModelLimit(tier_id=tier_id, image_model="gpt-image-1.5", monthly_requests=25),
        TierImageModelLimit(tier_id=tier_id, image_model="gpt-image-2", monthly_requests=25),
    ]
    tier.tier_image_quality_limits = [
        TierImageQualityLimit(tier_id=tier_id, quality="low"),
    ]

    response = tier_helpers._build_tier_response(tier, pricing_by_model={})
    by_model = {entry.image_model: entry.requests_limit for entry in response.tier_image_model_limits}
    assert by_model["gpt-image-1.5"] == -1
    assert by_model["gpt-image-2"] == -1
    assert response.daily_image_energy == 25
    assert response.image_energy_max == 125


def test_tier_response_exposes_energy_cost_per_quality():
    tier_id = uuid.uuid4()
    tier = SubscriptionTier(
        id=tier_id,
        name="tier",
        name_ru="tier-ru",
        description="d",
        description_ru="d",
        price_cents=100,
        monthly_images=100,
        daily_image_energy=100,
        monthly_docs=0,
        monthly_deepsearch=0,
        is_active=True,
        is_public=True,
        index=1,
        is_recurring=True,
    )
    tier.tier_model_limits = [TierModelLimit(tier_id=tier_id, model_name="gpt-5.4-nano", monthly_requests=1000)]
    tier.tier_image_model_limits = [TierImageModelLimit(tier_id=tier_id, image_model="gpt-image-1.5", monthly_requests=100)]
    tier.tier_image_quality_limits = [TierImageQualityLimit(tier_id=tier_id, quality="low")]

    pricing = ImageQualityPricing(image_model="gpt-image-1.5", quality="low", credit_cost=10.0)
    response = tier_helpers._build_tier_response(tier, pricing_by_model={"gpt-image-1.5": [pricing]})

    assert len(response.image_quality_pricing) == 1
    assert response.image_quality_pricing[0].credit_cost == 10.0
    assert response.image_quality_pricing[0].energy_cost == 10.0
