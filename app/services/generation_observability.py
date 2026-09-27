"""Report unexpected terminal chat failures, including handled stream errors."""

import logging

import sentry_sdk

from app.services.provider_errors import ImageModerationError, ProviderResponseError

logger = logging.getLogger(__name__)


def report_generation_failure(lifecycle, *, model, request_id, exception=None):
    if lifecycle.get("failure_reported"):
        return
    code = lifecycle.get("last_error_code") or "generation_failed"
    if isinstance(exception, ImageModerationError):
        code = exception.code
    elif isinstance(exception, ProviderResponseError):
        code = (
            "response_capacity_exceeded"
            if exception.reason in {"max_output_tokens", "max_tokens"}
            else "provider_response_incomplete"
        )
    elif isinstance(exception, TimeoutError):
        code = "generation_timeout"
    # Do not send provider payloads, prompts, partial answers or arbitrary error text.
    if code not in {
        "image_moderation_blocked",
        "response_capacity_exceeded",
        "provider_response_incomplete",
        "generation_timeout",
        "generation_failed",
        "request_spend_limit",
    }:
        code = "generation_failed"
    try:
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("chat.failure", "true")
            scope.set_tag("chat.model", model or "unknown")
            scope.set_tag("chat.error_code", code)
            scope.set_tag("chat.handled", "true")
            if isinstance(exception, ImageModerationError):
                scope.set_tag("chat.failure_kind", "provider_safety_rejection")
                scope.set_tag("chat.moderation_stage", exception.stage)
                scope.set_tag("chat.provider", "openai")
            scope.fingerprint = ["chat-generation-failed", model or "unknown", code]
            scope.set_context("chat_failure", {"request_id": request_id, "code": code})
            sentry_sdk.capture_message("Chat generation failed", level="error")
        lifecycle["failure_reported"] = True
    except Exception:
        # Observability must never prevent refunds or terminal stream publication.
        logger.warning("Could not report chat generation failure", exc_info=True)
