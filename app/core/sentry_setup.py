import logging
import os
from typing import Any

from sentry_sdk.integrations.openai import OpenAIIntegration


def build_sentry_openai_integrations(logger: logging.Logger) -> list[Any]:
    kwargs: dict[str, Any] = {"include_prompts": True}
    encoding_name = (os.getenv("SENTRY_OPENAI_TIKTOKEN_ENCODING") or "").strip()
    if encoding_name:
        kwargs["tiktoken_encoding_name"] = encoding_name

    try:
        return [OpenAIIntegration(**kwargs)]
    except Exception:
        logger.warning(
            "Failed to initialize Sentry OpenAI integration; continuing without OpenAI tracing.",
            exc_info=True,
        )
        return []
