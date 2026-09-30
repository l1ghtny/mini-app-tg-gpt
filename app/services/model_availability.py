"""Operational provider availability, independent of customer entitlements."""

from fastapi import HTTPException

from app.core.config import settings
from app.services.model_registry import TEXT_MODEL_PROVIDER, canonicalize_text_model


def text_model_available(model: str, *, provider: str | None = None) -> bool:
    provider = provider or TEXT_MODEL_PROVIDER.get(canonicalize_text_model(model))
    return provider != "anthropic" or settings.ANTHROPIC_ENABLED


def require_text_model_available(model: str) -> None:
    if not text_model_available(model):
        raise HTTPException(
            status_code=503,
            detail={
                "error": "model_temporarily_unavailable",
                "provider": "anthropic",
                "message": "Claude is temporarily unavailable. Choose another model.",
            },
        )
