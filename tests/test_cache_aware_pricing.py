from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.db.models import AiModelPricing
from app.services.openai_service import UsageTracker
from app.services.pricing_service import PricingService


@pytest.mark.asyncio
async def test_compute_costs_prices_cached_reads_and_cache_writes_separately(monkeypatch):
    pricing = AiModelPricing(
        provider="openai",
        model_name="gpt-5.6-luna",
        currency="USD",
        unit_price_input_per_1m=Decimal("1"),
        unit_price_cached_input_per_1m=Decimal("0.1"),
        unit_price_cache_write_per_1m=Decimal("1.25"),
        unit_price_output_per_1m=Decimal("6"),
        unit_price_reasoning_per_1m=Decimal("0"),
        unit_price_web_search_call=Decimal("0"),
        unit_price_image_generation=Decimal("0"),
    )
    service = PricingService(SimpleNamespace())

    async def fake_get_pricing(provider: str, model_name: str):
        return pricing

    monkeypatch.setattr(service, "get_pricing", fake_get_pricing)
    costs = await service.compute_costs(
        "openai",
        "gpt-5.6-luna",
        input_tokens=1_000_000,
        cached_input_tokens=800_000,
        cache_write_tokens=100_000,
        output_tokens=100_000,
        reasoning_tokens=0,
        web_search_calls=0,
        images_generated=0,
    )

    assert costs[1] == Decimal("0.200000")
    assert costs[2] == Decimal("0.080000")
    assert costs[3] == Decimal("0.125000")
    assert costs[-1] == Decimal("1.005000")


def test_usage_tracker_extracts_cache_details_from_completed_event():
    tracker = UsageTracker()
    tracker.apply_completed_event(
        SimpleNamespace(
            response=SimpleNamespace(
                usage=SimpleNamespace(
                    input_tokens=1_200,
                    output_tokens=350,
                    input_tokens_details=SimpleNamespace(
                        cached_tokens=900,
                        cache_write_tokens=100,
                    ),
                    output_tokens_details=SimpleNamespace(reasoning_tokens=50),
                )
            )
        )
    )

    assert tracker.input_tokens == 1_200
    assert tracker.cached_input_tokens == 900
    assert tracker.cache_write_tokens == 100
    assert tracker.output_tokens == 300
    assert tracker.reasoning_tokens == 50
