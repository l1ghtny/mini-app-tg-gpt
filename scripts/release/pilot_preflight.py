import os
import sys
from urllib.parse import urlsplit


REQUIRED = (
    "DATABASE_URL",
    "DATABASE_READ_URL",
    "REDIS_URL",
    "SECRET_KEY",
    "WEBAPP_URL",
    "WEB_AUTH_CALLBACK_URL",
    "WEB_AUTH_FROM_EMAIL",
    "PASSKEY_RP_ID",
    "PASSKEY_ALLOWED_ORIGINS",
    "TELEGRAM_OIDC_CLIENT_ID",
    "TELEGRAM_OIDC_CLIENT_SECRET",
    "TELEGRAM_OIDC_REDIRECT_URI",
    "SMTP_HOST",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "SENTRY_DSN",
)


def _origin(value: str) -> str:
    parsed = urlsplit(value)
    return (
        f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
    )


def main() -> int:
    failures: list[str] = []
    for name in REQUIRED:
        if not os.getenv(name, "").strip():
            failures.append(f"{name} is missing")

    callback = os.getenv("WEB_AUTH_CALLBACK_URL", "")
    webapp = os.getenv("WEBAPP_URL", "")
    if callback and webapp and _origin(callback) != _origin(webapp):
        failures.append("WEB_AUTH_CALLBACK_URL and WEBAPP_URL use different origins")
    if callback and not callback.rstrip("/").endswith("/auth/callback"):
        failures.append("WEB_AUTH_CALLBACK_URL must end with /auth/callback")

    allowed_origins = {
        item.strip().rstrip("/")
        for item in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
        if item.strip()
    }
    if webapp and _origin(webapp).rstrip("/") not in allowed_origins:
        failures.append("WEBAPP_URL origin is absent from CORS_ALLOWED_ORIGINS")

    if os.getenv("WEB_AUTH_ENABLED", "").lower() not in {"true", "1"}:
        failures.append("WEB_AUTH_ENABLED is not true")
    if os.getenv("TELEGRAM_OIDC_ENABLED", "").lower() not in {"true", "1"}:
        failures.append("TELEGRAM_OIDC_ENABLED is not true")
    if os.getenv("AUTH_COOKIE_SECURE", "").lower() not in {"true", "1"}:
        failures.append("AUTH_COOKIE_SECURE is not true")
    if os.getenv("AUTH_COOKIE_SAMESITE", "lax").lower() not in {"lax", "strict"}:
        failures.append("AUTH_COOKIE_SAMESITE must be lax or strict")
    if os.getenv("WEB_AUTH_TRUSTED_PROXY_CIDRS", "").strip() == "":
        failures.append("WEB_AUTH_TRUSTED_PROXY_CIDRS is empty")

    passkey_origins = {
        item.strip().rstrip("/")
        for item in os.getenv("PASSKEY_ALLOWED_ORIGINS", "").split(",")
        if item.strip()
    }
    if webapp and _origin(webapp).rstrip("/") not in passkey_origins:
        failures.append("WEBAPP_URL origin is absent from PASSKEY_ALLOWED_ORIGINS")

    oidc_redirect = os.getenv("TELEGRAM_OIDC_REDIRECT_URI", "")
    parsed_oidc_redirect = urlsplit(oidc_redirect)
    if oidc_redirect and parsed_oidc_redirect.scheme != "https":
        failures.append("TELEGRAM_OIDC_REDIRECT_URI must use https")
    if oidc_redirect and not parsed_oidc_redirect.path.endswith(
        "/api/v1/auth/telegram/oidc/callback"
    ):
        failures.append(
            "TELEGRAM_OIDC_REDIRECT_URI must end with /api/v1/auth/telegram/oidc/callback"
        )
    oidc_scopes = set(
        os.getenv("TELEGRAM_OIDC_SCOPES", "openid profile telegram:bot_access").split()
    )
    if not {"openid", "profile"}.issubset(oidc_scopes):
        failures.append("TELEGRAM_OIDC_SCOPES must include openid and profile")

    if failures:
        print("Pilot preflight failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print(
        "Pilot preflight passed; required values are present and internally consistent."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
