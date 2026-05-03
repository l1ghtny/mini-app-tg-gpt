from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.models_catalog as models_catalog_api
from app.api.dependencies import get_current_user
from app.db.database import get_session
from app.schemas.models_catalog import ModelsCatalogResponse


def _build_test_client() -> TestClient:
    app = FastAPI()
    app.include_router(models_catalog_api.models_catalog, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: object()

    async def _fake_get_session():
        yield None

    app.dependency_overrides[get_session] = _fake_get_session
    return TestClient(app)


def test_models_catalog_endpoint_returns_catalog(monkeypatch):
    captured = {}

    async def _fake_get_models_catalog(session, user):
        captured["session"] = session
        captured["user"] = user
        return ModelsCatalogResponse(
            text_models=[],
            image_models=[],
            updated_at=datetime(2026, 5, 3, 0, 0, 0),
        )

    monkeypatch.setattr(models_catalog_api.model_catalog_helpers, "get_models_catalog", _fake_get_models_catalog)

    client = _build_test_client()
    response = client.get("/api/v1/models/catalog")

    assert response.status_code == 200
    payload = response.json()
    assert payload["text_models"] == []
    assert payload["image_models"] == []
    assert payload["updated_at"] == "2026-05-03T00:00:00"
    assert "user" in captured
