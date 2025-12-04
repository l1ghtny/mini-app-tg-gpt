import os
from dotenv import load_dotenv, find_dotenv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]  # repo root

env_path = find_dotenv()

load_dotenv(find_dotenv(), override=True)


class Settings:
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY")
    DATABASE_URL: str = os.getenv("DATABASE_URL")
    SECRET_KEY: str = os.getenv("SECRET_KEY")
    BOT_TOKEN: str = os.getenv("BOT_TOKEN")
    DEBUG_MODE: bool = os.getenv("DEBUG_MODE", "False").lower() in ("true", "1")
    TEST_ENV: bool = os.getenv("TEST_ENV", "False").lower() in ("true", "1")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7
    TBANK_TERMINAL_KEY: str = os.getenv("TBANK_TERMINAL_KEY", "DEMO")
    TBANK_PASSWORD: str = os.getenv("TBANK_PASSWORD", "password")
    TBANK_API_URL: str = "https://securepay.tinkoff.ru/v2"

settings = Settings()