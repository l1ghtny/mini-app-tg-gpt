import uuid
from types import SimpleNamespace

import pytest

import app.api.chat_helpers as chat_helpers
import app.api.dependencies as dependencies


@pytest.mark.asyncio
async def test_require_text_entitlement_allows_infinite_remaining(monkeypatch):
    user = SimpleNamespace(id=uuid.uuid4())

    async def _fake_select_text_entitlement(_session, _user_id, _model):
        return {
            "remaining": -1,
            "tier_id": None,
            "usage_pack_id": None,
        }

    async def _unexpected_get_available_models(*_args, **_kwargs):
        raise AssertionError("get_available_models must not be called for infinite entitlement")

    monkeypatch.setattr(chat_helpers, "select_text_entitlement", _fake_select_text_entitlement)
    monkeypatch.setattr(chat_helpers, "get_available_models", _unexpected_get_available_models)

    entitlement = await chat_helpers._require_text_entitlement(None, user, "gpt-5.2")

    assert entitlement.remaining == -1
    assert entitlement.tier_id is None
    assert entitlement.usage_pack_id is None


@pytest.mark.asyncio
async def test_get_available_models_includes_infinite_remaining(monkeypatch):
    user = SimpleNamespace(id=uuid.uuid4())
    subscriptions = [
        SimpleNamespace(
            tier=SimpleNamespace(
                tier_model_limits=[
                    SimpleNamespace(model_name="gpt-5.2"),
                ]
            )
        )
    ]
    packs = [
        SimpleNamespace(
            pack=SimpleNamespace(
                pack_model_limits=[
                    SimpleNamespace(model_name="gpt-5-mini"),
                    SimpleNamespace(model_name="gpt-5-nano"),
                ]
            )
        )
    ]

    async def _fake_get_active_subscriptions(_session, _user_id):
        return subscriptions

    async def _fake_get_active_usage_packs(_session, _user_id):
        return packs

    async def _fake_select_text_entitlement(_session, _user_id, model_name):
        if model_name == "gpt-5.2":
            return {"remaining": -1}
        if model_name == "gpt-5-nano":
            return {"remaining": 3}
        return {"remaining": 0}

    monkeypatch.setattr(dependencies, "get_active_subscriptions", _fake_get_active_subscriptions)
    monkeypatch.setattr(dependencies, "get_active_usage_packs", _fake_get_active_usage_packs)
    monkeypatch.setattr(dependencies, "select_text_entitlement", _fake_select_text_entitlement)

    models = await dependencies.get_available_models(user, None)

    assert set(models) == {"gpt-5.2", "gpt-5-nano"}
