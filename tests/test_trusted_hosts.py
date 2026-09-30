from fastapi.testclient import TestClient
import pytest

from main import app


@pytest.fixture
def rebuild_test_db() -> None:
    """This middleware-only test does not need the database reset fixture."""


@pytest.mark.parametrize("host", [
    "tg-mini-backend-canary.gpt.svc.cluster.local",
    "beta.app.lightnyai.ru",
    "lightnyai-beta.internal",
])
def test_service_host_is_trusted(host: str) -> None:
    with TestClient(app) as client:
        response = client.get(
            "/health/live",
            headers={"host": host},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unrelated_host_is_rejected() -> None:
    with TestClient(app) as client:
        response = client.get("/health/live", headers={"host": "beta.app.lightnyai.ru.evil.example"})
    assert response.status_code == 400
