from fastapi.testclient import TestClient
import pytest

from main import app


@pytest.fixture
def rebuild_test_db() -> None:
    """This middleware-only test does not need the database reset fixture."""


def test_canary_service_host_is_trusted() -> None:
    with TestClient(app) as client:
        response = client.get(
            "/health/live",
            headers={"host": "tg-mini-backend-canary.gpt.svc.cluster.local"},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
