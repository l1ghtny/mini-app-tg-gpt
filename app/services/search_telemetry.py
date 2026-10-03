import re
from typing import Any


def redact_search_request_urls(value: Any, _hint: Any = None) -> Any:
    if isinstance(value, str):
        return re.sub(
            r"(/(?:conversations|chat-folders)/search/)[^\s?#]*", r"\1:query", value
        )
    if isinstance(value, list):
        return [redact_search_request_urls(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_search_request_urls(item) for key, item in value.items()}
    return value
