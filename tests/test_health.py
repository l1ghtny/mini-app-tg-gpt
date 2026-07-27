from unittest.mock import AsyncMock

import pytest

from app.api import health as health_module


@pytest.mark.asyncio
async def test_liveness_is_process_only():
    assert await health_module.live() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readiness_requires_database_and_redis(monkeypatch):
    monkeypatch.setattr(health_module, "_check_database", AsyncMock())
    monkeypatch.setattr(health_module, "_check_redis", AsyncMock())

    response = await health_module.ready()

    assert response.status_code == 200
    assert b'"status":"ready"' in response.body


@pytest.mark.asyncio
async def test_readiness_reports_dependency_failure_without_provider_restart(monkeypatch):
    database = AsyncMock(side_effect=RuntimeError("database down"))
    monkeypatch.setattr(health_module, "_check_database", database)
    monkeypatch.setattr(health_module, "_check_redis", AsyncMock())

    response = await health_module.ready()

    assert response.status_code == 503
    assert b'"database":"unavailable"' in response.body
    assert b'"providers":"not_required_for_readiness"' in response.body
