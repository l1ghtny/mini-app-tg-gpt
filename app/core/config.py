import os
from dotenv import load_dotenv, find_dotenv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]  # repo root

env_path = find_dotenv()

load_dotenv(find_dotenv(), override=True)


class Settings:
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY")
    DATABASE_URL: str = os.getenv("DATABASE_URL")

settings = Settings()