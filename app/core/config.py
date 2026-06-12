import logging
import os
from dotenv import load_dotenv, find_dotenv
from pathlib import Path

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
    GEMINI_API_BASE_URL: str = os.getenv("GEMINI_API_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
    GEMINI_PROXY_URL: str = (
        os.getenv("GEMINI_PROXY_URL")
        or os.getenv("GOOGLE_PROXY_URL")
        or os.getenv("https_proxy")
        or os.getenv("all_proxy")
        or os.getenv("http_proxy")
    )
    DATABASE_URL: str = os.getenv("TEST_DATABASE_URL") if TEST_ENV else os.getenv("DATABASE_URL")
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
    TBANK_TERMINAL_KEY: str = os.getenv("TBANK_TERMINAL_KEY", "DEMO")
    TBANK_PASSWORD: str = os.getenv("TBANK_PASSWORD", "password")
    TBANK_API_URL: str = os.getenv("TBANK_API_URL", "https://securepay.tinkoff.ru/v2")
    TBANK_TIMEOUT_SECONDS: float = float(os.getenv("TBANK_TIMEOUT_SECONDS", "15"))
    custom_logger = logging.getLogger("uvicorn")
    # Add Sentry Config
    SENTRY_DSN: str = os.getenv("SENTRY_DSN", "")
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "local")  # e.g. 'production', 'staging'
    TBANK_TAXATION: str = "usn_income"
    STARTER_BUNDLE_NAME: str = os.getenv("STARTER_BUNDLE")
    WEBAPP_URL: str = os.getenv("WEBAPP_URL")
    BOT_TOKEN_TEST_BOT: str = os.getenv("BOT_TOKEN_TEST_BOT")
    BROADCAST_ADMIN_TOKEN: str = os.getenv("BROADCAST_ADMIN_TOKEN", "")
    OPENAI_CHAINING_ENABLED: bool = os.getenv("OPENAI_CHAINING_ENABLED", "False").lower() in ("true", "1")
    OPENAI_CHAIN_MAX_INACTIVITY_DAYS: int = int(os.getenv("OPENAI_CHAIN_MAX_INACTIVITY_DAYS", "14"))
    DOCUMENT_PROVIDER_DEFAULT: str = os.getenv("DOCUMENT_PROVIDER_DEFAULT", "openai")
    GOOGLE_DOCUMENTS_ENABLED: bool = os.getenv("GOOGLE_DOCUMENTS_ENABLED", "False").lower() in ("true", "1")
    DOCUMENT_PROVIDER_FALLBACK_ENABLED: bool = os.getenv("DOCUMENT_PROVIDER_FALLBACK_ENABLED", "True").lower() in ("true", "1")
    DOCUMENT_DUAL_INDEX_ENABLED: bool = os.getenv("DOCUMENT_DUAL_INDEX_ENABLED", "False").lower() in ("true", "1")

settings = Settings()
