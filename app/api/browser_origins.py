"""Explicit browser destinations shared by login methods.

CORS permission alone never grants permission to receive login callbacks.
"""
from urllib.parse import urlsplit

from fastapi import HTTPException

from app.core.config import settings


def resolve_browser_origin(origin: str | None = None) -> str:
    legacy = (settings.WEBAPP_URL or "").rstrip("/")
    selected = origin.rstrip("/") if origin else legacy
    if not selected:
        raise HTTPException(status_code=503, detail="web_login_not_configured")
    if selected == legacy:
        return selected
    parts = urlsplit(selected)
    if (
        selected not in settings.WEB_AUTH_ADDITIONAL_ORIGINS
        or parts.scheme != "https"
        or not parts.hostname
        or parts.username or parts.password
        or parts.path or parts.query or parts.fragment
    ):
        raise HTTPException(status_code=403, detail="web_login_origin_not_allowed")
    return selected


def telegram_callback(origin: str) -> str:
    selected = resolve_browser_origin(origin)
    if selected == (settings.WEBAPP_URL or "").rstrip("/"):
        return settings.TELEGRAM_OIDC_REDIRECT_URI
    return f"{selected}/api/v1/auth/telegram/oidc/callback"
