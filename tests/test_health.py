from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api import health as health_module


@pytest.mark.asyncio
@pytest.mark.parametrize("writable", [True, False])
async def test_readiness_rejects_demoted_primary_and_discards_stale_pool(monkeypatch, writable):
    session = AsyncMock()
    session.execute.return_value = MagicMock()
    session.execute.return_value.scalar_one.return_value = writable
    context = AsyncMock()
    context.__aenter__.return_value = session
    monkeypatch.setattr(health_module, "AsyncSession", lambda engine: context)
    dispose = AsyncMock()
    monkeypatch.setattr(health_module, "engine", MagicMock(dispose=dispose))
    monkeypatch.setattr(health_module, "_check_redis", AsyncMock())

    response = await health_module.ready()

    assert response.status_code == (200 if writable else 503)
    if writable:
        session.invalidate.assert_not_awaited()
        dispose.assert_not_awaited()
    else:
        session.invalidate.assert_awaited_once()
        dispose.assert_awaited_once()
        assert b'"database":"unavailable"' in response.body


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
