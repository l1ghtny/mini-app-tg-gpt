from types import SimpleNamespace
from unittest.mock import AsyncMock
from datetime import datetime

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.services import allowance, model_availability


@pytest.mark.parametrize("model", ["claude-sonnet-5", "claude-opus-5", "claude-fable-5-1"])
def test_pause_preserves_catalog_and_other_providers(monkeypatch, model):
    monkeypatch.setattr(settings, "ANTHROPIC_ENABLED", False)
    entries = {m["model_name"]: m for m in allowance.catalog()["text_models"]}
    assert entries[model]["available"] is False
    assert entries["gpt-5.6-terra"]["available"] is True
    with pytest.raises(HTTPException) as error:
        model_availability.require_text_model_available(model)
    assert error.value.status_code == 503
    assert error.value.detail["error"] == "model_temporarily_unavailable"
    monkeypatch.setattr(settings, "ANTHROPIC_ENABLED", True)
    assert model_availability.text_model_available(model)
    model_availability.require_text_model_available(model)


@pytest.mark.asyncio
async def test_legacy_catalog_reports_pause(monkeypatch):
    from app.api.model_catalog_helpers import get_models_catalog

    monkeypatch.setattr(settings, "ANTHROPIC_ENABLED", False)
    row = SimpleNamespace(
        model_name="claude-sonnet-5", display_name="Claude", display_name_ru=None,
        provider="anthropic", tagline=None, tagline_ru=None, description=None,
        description_ru=None, best_for=[], best_for_ru=[], not_great_for=[],
        not_great_for_ru=[], speed=None, intelligence=None, context_window=None,
        supports={}, tier_required=None, badges=[], credit_cost_hint=None,
        updated_at=datetime(2026, 9, 30),
    )
    session = SimpleNamespace(exec=AsyncMock(side_effect=[
        SimpleNamespace(all=lambda: [row]), SimpleNamespace(all=lambda: []),
        SimpleNamespace(all=lambda: []),
    ]))
    catalog = await get_models_catalog(session)
    assert catalog.text_models[0].available is False


@pytest.mark.asyncio
async def test_pause_stops_send_before_mutations_but_keeps_idempotent_result(monkeypatch):
    from app.api import chat_helpers

    monkeypatch.setattr(settings, "ANTHROPIC_ENABLED", False)
    existing = AsyncMock(return_value=None)
    load = AsyncMock()
    monkeypatch.setattr(chat_helpers, "_get_idempotency_response", existing)
    monkeypatch.setattr(chat_helpers, "_load_conversation_for_user", load)
    args = dict(conversation_id="chat", request=SimpleNamespace(
        client_request_id="request", model="claude-sonnet-5"),
        background_tasks=None, session=None, current_user=SimpleNamespace(id="user"), bus=None)
    with pytest.raises(HTTPException):
        await chat_helpers.handle_create_message(**args)
    load.assert_not_awaited()
    existing.return_value = "previously accepted result"
    assert await chat_helpers.handle_create_message(**args) == "previously accepted result"


@pytest.mark.asyncio
async def test_provider_guard_stops_context_and_upstream_attempt(monkeypatch):
    from app.services import shared_chat_provider as provider

    monkeypatch.setattr(settings, "ANTHROPIC_ENABLED", False)
    compress = AsyncMock()
    monkeypatch.setattr(provider, "compress_context", compress)
    with pytest.raises(HTTPException):
        async for _ in provider.stream_shared_response([], "claude-sonnet-5"):
            pass
    compress.assert_not_awaited()
    run = SimpleNamespace(start=AsyncMock(), text_capacity=AsyncMock())
    with pytest.raises(HTTPException):
        async for _ in provider.claude_turn(run, [], "claude-sonnet-5", "", {}, None, "low", 0):
            pass
    run.start.assert_not_awaited()
    run.text_capacity.assert_not_awaited()
