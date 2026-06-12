from urllib.parse import urlsplit, urlunsplit

import aioboto3
from botocore.config import Config

from app.r2.settings import Settings

R2_BUCKET = Settings.R2_BUCKET
R2_REGION = Settings.R2_REGION
R2_ACCESS_KEY_ID = Settings.R2_ACCESS_KEY_ID
R2_SECRET_ACCESS_KEY = Settings.R2_SECRET_ACCESS_KEY

# One session reused across awaits
_session = aioboto3.Session()


def _normalize_r2_endpoint(endpoint: str) -> str:
    normalized = (endpoint or "").strip().rstrip("/")
    if not normalized:
        return normalized

    parsed = urlsplit(normalized)
    if not parsed.scheme or not parsed.netloc:
        return normalized

    if not parsed.path or parsed.path == "/":
        return normalized

    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


R2_ENDPOINT = _normalize_r2_endpoint(Settings.R2_ENDPOINT)  # e.g. https://<ACCOUNT_ID>.r2.cloudflarestorage.com


def _client_kwargs():
    return dict(
        service_name="s3",
        region_name=R2_REGION,
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"}),
    )


# Lightweight factory: `async with s3_client() as s3: ...`
def s3_client():
    return _session.client(**_client_kwargs())
