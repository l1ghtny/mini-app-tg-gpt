import os


class Settings:
    R2_BUCKET = os.environ["R2_BUCKET"]
    R2_ENDPOINT = os.environ["R2_ENDPOINT"]
    R2_REGION = os.getenv("R2_REGION", "auto")
    R2_ACCESS_KEY_ID = os.environ["R2_ACCESS_KEY_ID"]
    R2_SECRET_ACCESS_KEY = os.environ["R2_SECRET_ACCESS_KEY"]
    R2_PUBLIC_BASE_URL = os.getenv("R2_PUBLIC_BASE_URL")  # optional


