from app.services.search_telemetry import redact_search_request_urls


def test_redacts_search_urls_in_events_spans_and_logs_without_mutating_inputs():
    event = {
        "request": {
            "url": "https://app.example/api/v1/conversations/search/private%20query"
        },
        "spans": [
            {
                "description": "GET /api/v1/chat-folders/search/secret",
                "data": {"http.target": "/api/v1/conversations/search/secret"},
            }
        ],
        "body": "GET /api/v1/conversations/search/private HTTP/1.1",
        "release": "2.0.2",
    }
    redacted = redact_search_request_urls(event, {})
    assert "private" not in str(redacted)
    assert "secret" not in str(redacted)
    assert "/search/:query" in str(redacted)
    assert redacted["release"] == event["release"]
    assert "private" in event["request"]["url"]
