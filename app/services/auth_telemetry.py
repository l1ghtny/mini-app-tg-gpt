"""Keep login codes, provider tokens and browser credentials out of Sentry."""

import re
from typing import Any

from app.services.search_telemetry import redact_search_request_urls

_AUTH_PATH = re.compile(r"/api/v1/auth/[^\s\"'<>?#]*\?[^\s\"'<>]*")
_PROVIDER_URL = re.compile(
    r"https://(?:oauth\.yandex\.ru/token|login\.yandex\.ru/info|oauth\.telegram\.org/token)(?:\?[^\s\"'<>]*)?"
)
_SECRET_HEADERS = {"authorization", "cookie", "set-cookie"}


def _auth_url(url: Any) -> bool:
    return isinstance(url, str) and (
        "/api/v1/auth/" in url or bool(_PROVIDER_URL.search(url))
    )


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return _AUTH_PATH.sub(lambda m: m[0].split("?", 1)[0], value)
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, dict):
        result = {key: _redact(item) for key, item in value.items()}
        if _auth_url(value.get("url")):
            for key in ("query_string", "data", "body", "cookies", "response"):
                result.pop(key, None)
            result["url"] = value["url"].split("?", 1)[0]
            headers = result.get("headers")
            if isinstance(headers, dict):
                result["headers"] = {
                    k: v for k, v in headers.items() if k.lower() not in _SECRET_HEADERS
                }
            elif headers is not None:
                result.pop("headers", None)
        return result
    return value


def redact_request_telemetry(value: Any, _hint: Any = None) -> Any:
    return _redact(redact_search_request_urls(value))
