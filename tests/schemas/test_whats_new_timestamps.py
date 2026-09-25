from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.schemas.whats_new import WhatsNewListResponse, WhatsNewSeenResponse


@pytest.mark.parametrize(
    "timestamp",
    [
        datetime(2026, 9, 24, 9, 0, 0, 643848),
        datetime(2026, 9, 24, 9, 0, 0, 643848, tzinfo=UTC),
        datetime(2026, 9, 24, 12, 0, 0, 643848, tzinfo=timezone(timedelta(hours=3))),
        datetime(2026, 9, 24, 5, 0, 0, 643848, tzinfo=timezone(timedelta(hours=-4))),
        "2026-09-24T09:00:00.643848",
        "2026-09-24T12:00:00.643848+03:00",
    ],
)
def test_feed_and_seen_responses_use_explicit_utc(timestamp):
    app = FastAPI()

    @app.get("/whats-new", response_model=WhatsNewListResponse)
    def feed():
        return {
            "items": [
                {
                    "id": "timezone-fix",
                    "published_at": timestamp,
                    "kind": "fix",
                    "title": "Timestamp test",
                    "body": "Test body",
                }
            ],
            "latest_published_at": timestamp,
            "seen_up_to": timestamp,
        }

    @app.post("/whats-new/seen", response_model=WhatsNewSeenResponse)
    def seen():
        return WhatsNewSeenResponse(seen_up_to=timestamp)

    with TestClient(app) as client:
        response = client.get("/whats-new")
        assert response.status_code == 200
        payload = response.json()
        expected = "2026-09-24T09:00:00.643848Z"
        assert payload["items"][0]["published_at"] == expected
        assert payload["latest_published_at"] == expected
        assert payload["seen_up_to"] == expected
        assert client.post("/whats-new/seen").json()["seen_up_to"] == expected


def test_missing_feed_and_seen_timestamps_stay_null():
    assert WhatsNewListResponse().model_dump(mode="json") == {
        "items": [],
        "latest_published_at": None,
        "seen_up_to": None,
        "has_unseen": False,
        "unseen_count": 0,
    }
    assert WhatsNewSeenResponse().model_dump(mode="json") == {"seen_up_to": None}
