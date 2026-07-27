from types import SimpleNamespace
from datetime import UTC, datetime

import pytest

import app.api.public_catalog as public_catalog_api


@pytest.mark.asyncio
async def test_public_catalog_combines_catalogues_without_user_auth(monkeypatch):
    async def fake_models(_session):
        return {
            "text_models": [],
            "image_models": [],
            "provider_defaults": {},
            "updated_at": datetime.now(UTC),
        }

    async def fake_tiers(_session):
        return []

    async def fake_packs(_session):
        return []

    monkeypatch.setattr(public_catalog_api.model_catalog_helpers, "get_models_catalog", fake_models)
    monkeypatch.setattr(public_catalog_api.tier_helpers, "list_public_tiers_for_catalog", fake_tiers)
    monkeypatch.setattr(public_catalog_api.usage_pack_helpers, "list_public_packs", fake_packs)

    response = SimpleNamespace(headers={})
    result = await public_catalog_api.get_public_product_catalog(response, SimpleNamespace())

    assert result.billing_contract.unit == "completed_text_answer"
    assert result.billing_contract.tokens_visible_to_user is False
    assert result.tiers == []
    assert response.headers["Cache-Control"].startswith("public")
