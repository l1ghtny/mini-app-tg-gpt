import os


class Settings:
    R2_BUCKET = os.environ["R2_BUCKET"]
    R2_ENDPOINT = os.environ["R2_ENDPOINT"]
    R2_REGION = os.getenv("R2_REGION", "auto")
    R2_ACCESS_KEY_ID = os.environ["R2_ACCESS_KEY_ID"]
    R2_SECRET_ACCESS_KEY = os.environ["R2_SECRET_ACCESS_KEY"]
    R2_PRIVATE_DOCUMENTS_BUCKET = os.getenv(
        "R2_PRIVATE_DOCUMENTS_BUCKET",
        "",
    ).strip()
    R2_PRIVATE_DOCUMENTS_ACCESS_KEY_ID = os.getenv(
        "R2_PRIVATE_DOCUMENTS_ACCESS_KEY_ID",
        "",
    ).strip()
    R2_PRIVATE_DOCUMENTS_SECRET_ACCESS_KEY = os.getenv(
        "R2_PRIVATE_DOCUMENTS_SECRET_ACCESS_KEY",
        "",
    ).strip()
    R2_PRIVATE_DOCUMENTS_SESSION_TOKEN = os.getenv(
        "R2_PRIVATE_DOCUMENTS_SESSION_TOKEN",
        "",
    ).strip()
    R2_PRIVATE_DOCUMENTS_CREDENTIAL_EXPIRES_AT = os.getenv(
        "R2_PRIVATE_DOCUMENTS_CREDENTIAL_EXPIRES_AT",
        "",
    ).strip()
    R2_PUBLIC_BASE_URL = os.getenv("R2_PUBLIC_BASE_URL")  # optional
    R2_OPENAI_PUBLIC_BASE_URL = (
        os.getenv("R2_OPENAI_PUBLIC_BASE_URL") or R2_PUBLIC_BASE_URL
    )
