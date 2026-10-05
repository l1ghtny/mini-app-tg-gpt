import os
from pathlib import Path

APP_VERSION = "2.3.0"


def sentry_release(component: str) -> str:
    build_file = Path(__file__).resolve().parents[2] / ".build-id"
    build = os.getenv("APP_BUILD_ID") or (
        build_file.read_text().strip() if build_file.exists() else "local"
    )
    return f"{component}@{APP_VERSION}+{build}"
