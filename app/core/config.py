import logging
import os
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]  # repo root
_PROXY_ENV_ALIASES = (
    ("http_proxy", "HTTP_PROXY"),
    ("https_proxy", "HTTPS_PROXY"),
    ("all_proxy", "ALL_PROXY"),
    ("no_proxy", "NO_PROXY"),
)

TEST_ENV = os.getenv("TEST_ENV", "False").lower() in ("true", "1")

if TEST_ENV:
    load_dotenv(BASE_DIR / ".env.test", override=True)
else:
    load_dotenv(find_dotenv(), override=True)


def _normalize_proxy_env_aliases() -> None:
    for lower_name, upper_name in _PROXY_ENV_ALIASES:
        value = os.getenv(lower_name) or os.getenv(upper_name)
        if not value:
            continue
        os.environ[lower_name] = value
        os.environ[upper_name] = value


_normalize_proxy_env_aliases()


class Settings:
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    GEMINI_API_BASE_URL: str = os.getenv(
        "GEMINI_API_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"
    )
    PERPLEXITY_API_KEY: str = os.getenv("PERPLEXITY_API_KEY", "")
    PERPLEXITY_API_BASE_URL: str = os.getenv(
        "PERPLEXITY_API_BASE_URL", "https://api.perplexity.ai"
    )
    PERPLEXITY_SEARCH_CONTEXT_SIZE: str = os.getenv(
        "PERPLEXITY_SEARCH_CONTEXT_SIZE", "low"
    )
    GEMINI_PROXY_URL: str = (
        os.getenv("GEMINI_PROXY_URL")
        or os.getenv("GOOGLE_PROXY_URL")
        or os.getenv("https_proxy")
        or os.getenv("all_proxy")
        or os.getenv("http_proxy")
    )
    DATABASE_URL: str = (
        os.getenv("TEST_DATABASE_URL") if TEST_ENV else os.getenv("DATABASE_URL")
    )
    DATABASE_READ_URL: str = (
        os.getenv("TEST_DATABASE_URL")
        if TEST_ENV
        else (os.getenv("DATABASE_READ_URL") or os.getenv("DATABASE_URL"))
    )
    SECRET_KEY: str = os.getenv("SECRET_KEY")
    BOT_TOKEN: str = os.getenv("BOT_TOKEN")
    DEBUG_MODE: bool = os.getenv("DEBUG_MODE", "False").lower() in ("true", "1")
    TEST_ENV: bool = os.getenv("TEST_ENV", "False").lower() in ("true", "1")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 4
    CORS_ALLOWED_ORIGINS: tuple[str, ...] = tuple(
        origin.strip()
        for origin in os.getenv(
            "CORS_ALLOWED_ORIGINS",
            ",".join(
                (
                    "http://localhost:5172",
                    "http://127.0.0.1:5172",
                    "http://localhost:5173",
                    "http://127.0.0.1:5173",
                    "http://localhost:4173",
                    "http://127.0.0.1:4173",
                    "http://localhost:4175",
                    "http://127.0.0.1:4175",
                    "http://192.168.1.137:5173",
                    "http://192.168.1.137:4173",
                    "https://gpt-mini-app.lightny.pro",
                    "https://gpt-mini-app-ru.lightny.pro",
                    "https://gpt-mini-app-dev.lightny.pro",
                    "https://preview--chat-bot-telegram.lovable.app",
                    "https://lightny.ru",
                    "https://www.lightny.ru",
                    "https://app.lightny.ru",
                )
            ),
        ).split(",")
        if origin.strip()
    )
    WEB_AUTH_ENABLED: bool = os.getenv("WEB_AUTH_ENABLED", "False").lower() in (
        "true",
        "1",
    )
    WEB_AUTH_LINK_TTL_MINUTES: int = int(os.getenv("WEB_AUTH_LINK_TTL_MINUTES", "15"))
    WEB_AUTH_CALLBACK_URL: str = os.getenv("WEB_AUTH_CALLBACK_URL", "")
    WEB_AUTH_FROM_EMAIL: str = os.getenv("WEB_AUTH_FROM_EMAIL", "")
    TELEGRAM_OIDC_ENABLED: bool = os.getenv(
        "TELEGRAM_OIDC_ENABLED", "False"
    ).lower() in ("true", "1")
    TELEGRAM_OIDC_CLIENT_ID: str = os.getenv("TELEGRAM_OIDC_CLIENT_ID", "").strip()
    TELEGRAM_OIDC_CLIENT_SECRET: str = os.getenv(
        "TELEGRAM_OIDC_CLIENT_SECRET", ""
    ).strip()
    TELEGRAM_OIDC_REDIRECT_URI: str = os.getenv(
        "TELEGRAM_OIDC_REDIRECT_URI", ""
    ).strip()
    TELEGRAM_OIDC_SCOPES: str = os.getenv(
        "TELEGRAM_OIDC_SCOPES",
        "openid profile telegram:bot_access",
    ).strip()
    TELEGRAM_OIDC_STATE_TTL_SECONDS: int = int(
        os.getenv("TELEGRAM_OIDC_STATE_TTL_SECONDS", "600")
    )
    TELEGRAM_OIDC_HTTP_TIMEOUT_SECONDS: float = float(
        os.getenv("TELEGRAM_OIDC_HTTP_TIMEOUT_SECONDS", "10")
    )
    TELEGRAM_OIDC_ISSUER: str = os.getenv(
        "TELEGRAM_OIDC_ISSUER", "https://oauth.telegram.org"
    )
    TELEGRAM_OIDC_AUTH_URL: str = os.getenv(
        "TELEGRAM_OIDC_AUTH_URL", "https://oauth.telegram.org/auth"
    )
    TELEGRAM_OIDC_TOKEN_URL: str = os.getenv(
        "TELEGRAM_OIDC_TOKEN_URL", "https://oauth.telegram.org/token"
    )
    TELEGRAM_OIDC_JWKS_URL: str = os.getenv(
        "TELEGRAM_OIDC_JWKS_URL",
        "https://oauth.telegram.org/.well-known/jwks.json",
    )
    PASSKEY_RP_ID: str = os.getenv("PASSKEY_RP_ID", "").strip().lower()
    PASSKEY_RP_NAME: str = (
        os.getenv("PASSKEY_RP_NAME", "Lightny AI").strip() or "Lightny AI"
    )
    PASSKEY_CHALLENGE_TTL_SECONDS: int = int(
        os.getenv("PASSKEY_CHALLENGE_TTL_SECONDS", "300")
    )
    PASSKEY_ALLOWED_ORIGINS: tuple[str, ...] = tuple(
        origin.strip().rstrip("/")
        for origin in os.getenv(
            "PASSKEY_ALLOWED_ORIGINS", ",".join(CORS_ALLOWED_ORIGINS)
        ).split(",")
        if origin.strip()
    )
    AUTH_COOKIE_NAME: str = os.getenv("AUTH_COOKIE_NAME", "lightny_session")
    AUTH_COOKIE_SECURE: bool = os.getenv(
        "AUTH_COOKIE_SECURE",
        "False" if DEBUG_MODE or TEST_ENV else "True",
    ).lower() in ("true", "1")
    AUTH_COOKIE_SAMESITE: str = os.getenv("AUTH_COOKIE_SAMESITE", "lax").lower()
    AUTH_COOKIE_DOMAIN: str | None = os.getenv("AUTH_COOKIE_DOMAIN") or None
    BROWSER_SESSION_TTL_DAYS: int = int(os.getenv("BROWSER_SESSION_TTL_DAYS", "30"))
    AUTH_COOKIE_MAX_AGE_SECONDS: int = int(
        os.getenv(
            "AUTH_COOKIE_MAX_AGE_SECONDS", str(BROWSER_SESSION_TTL_DAYS * 24 * 60 * 60)
        )
    )
    WEB_AUTH_TRUSTED_PROXY_CIDRS: tuple[str, ...] = tuple(
        cidr.strip()
        for cidr in os.getenv("WEB_AUTH_TRUSTED_PROXY_CIDRS", "").split(",")
        if cidr.strip()
    )
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME: str = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_STARTTLS: bool = os.getenv("SMTP_STARTTLS", "True").lower() in ("true", "1")
    TBANK_TERMINAL_KEY: str = os.getenv("TBANK_TERMINAL_KEY", "DEMO")
    TBANK_PASSWORD: str = os.getenv("TBANK_PASSWORD", "password")
    TBANK_API_URL: str = os.getenv("TBANK_API_URL", "https://securepay.tinkoff.ru/v2")
    TBANK_TIMEOUT_SECONDS: float = float(os.getenv("TBANK_TIMEOUT_SECONDS", "15"))
    custom_logger = logging.getLogger("uvicorn")
    # Add Sentry Config
    SENTRY_DSN: str = os.getenv("SENTRY_DSN", "")
    SENTRY_AUTH_TOKEN: str = os.getenv("SENTRY_AUTH_TOKEN", "")
    SENTRY_ORG: str = os.getenv("SENTRY_ORG", "")
    SENTRY_PROJECT: str = os.getenv("SENTRY_PROJECT", "")
    SENTRY_BASE_URL: str = os.getenv("SENTRY_BASE_URL", "https://sentry.io")
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "local")  # e.g. 'production', 'staging'
    TBANK_TAXATION: str = "usn_income"
    STARTER_BUNDLE_NAME: str = os.getenv("STARTER_BUNDLE")
    WEBAPP_URL: str = os.getenv("WEBAPP_URL")
    BOT_TOKEN_TEST_BOT: str = os.getenv("BOT_TOKEN_TEST_BOT")
    BROADCAST_ADMIN_TOKEN: str = os.getenv("BROADCAST_ADMIN_TOKEN", "")
    BROADCAST_ADMIN_TELEGRAM_ALLOWLIST: str = os.getenv(
        "BROADCAST_ADMIN_TELEGRAM_ALLOWLIST", ""
    )
    OPENAI_CHAINING_ENABLED: bool = os.getenv(
        "OPENAI_CHAINING_ENABLED", "False"
    ).lower() in ("true", "1")
    OPENAI_CHAIN_MAX_INACTIVITY_DAYS: int = int(
        os.getenv("OPENAI_CHAIN_MAX_INACTIVITY_DAYS", "14")
    )
    DOCUMENT_PROVIDER_DEFAULT: str = os.getenv("DOCUMENT_PROVIDER_DEFAULT", "openai")
    GOOGLE_DOCUMENTS_ENABLED: bool = os.getenv(
        "GOOGLE_DOCUMENTS_ENABLED", "False"
    ).lower() in ("true", "1")
    DOCUMENT_PROVIDER_FALLBACK_ENABLED: bool = os.getenv(
        "DOCUMENT_PROVIDER_FALLBACK_ENABLED", "True"
    ).lower() in ("true", "1")
    DOCUMENT_DUAL_INDEX_ENABLED: bool = os.getenv(
        "DOCUMENT_DUAL_INDEX_ENABLED", "False"
    ).lower() in ("true", "1")


settings = Settings()
