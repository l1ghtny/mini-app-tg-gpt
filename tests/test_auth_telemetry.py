from app.services.auth_telemetry import redact_request_telemetry


def test_callback_credentials_are_removed_without_hiding_the_failure():
    event = {
        "request": {
            "url": "https://app.example.com/api/v1/auth/yandex/callback?code=private&state=private-state",
            "query_string": "code=private",
            "cookies": {"lightny_session": "private-cookie"},
            "headers": {
                "Cookie": "private-cookie",
                "Authorization": "private-token",
                "User-Agent": "test",
            },
        },
        "message": "GET /api/v1/auth/yandex/callback?code=private&state=private-state HTTP/1.1 302",
        "exception": {
            "values": [{"type": "HTTPException", "value": "yandex_login_state_invalid"}]
        },
    }
    cleaned = redact_request_telemetry(event)
    assert "private" not in str(cleaned)
    assert cleaned["request"]["headers"] == {"User-Agent": "test"}
    assert cleaned["exception"] == event["exception"]
    assert event["request"]["query_string"] == "code=private"


def test_provider_exchange_and_login_bodies_are_not_sent_to_sentry():
    for url in (
        "https://oauth.yandex.ru/token",
        "https://login.yandex.ru/info",
        "https://oauth.telegram.org/token",
        "https://app.example.com/api/v1/auth/web/email/verify",
    ):
        cleaned = redact_request_telemetry(
            {
                "url": url,
                "data": {"client_secret": "private"},
                "body": "private",
                "response": {"access_token": "private"},
                "headers": {"Authorization": "private"},
                "status_code": 502,
            }
        )
        assert "private" not in str(cleaned)
        assert cleaned["status_code"] == 502


def test_search_redaction_and_non_auth_diagnostics_are_preserved():
    event = {
        "message": "GET /conversations/search/private-query 200",
        "request": {
            "url": "https://app.example.com/api/v1/messages?limit=20",
            "data": {"model": "gpt"},
        },
    }
    cleaned = redact_request_telemetry(event)
    assert "private-query" not in cleaned["message"]
    assert cleaned["request"] == event["request"]
