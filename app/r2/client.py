import aioboto3
from botocore.config import Config

from app.r2.settings import Settings

R2_BUCKET = Settings.R2_BUCKET
R2_ENDPOINT = Settings.R2_ENDPOINT              # e.g. https://<ACCOUNT_ID>.r2.cloudflarestorage.com
R2_REGION = Settings.R2_REGION
R2_ACCESS_KEY_ID = Settings.R2_ACCESS_KEY_ID
R2_SECRET_ACCESS_KEY = Settings.R2_SECRET_ACCESS_KEY

# One session reused across awaits
_session = aioboto3.Session()

def _client_kwargs(
    *,
    endpoint_url: str = R2_ENDPOINT,
    region_name: str = R2_REGION,
    access_key_id: str = R2_ACCESS_KEY_ID,
    secret_access_key: str = R2_SECRET_ACCESS_KEY,
):
    return dict(
        service_name="s3",
        region_name=region_name,
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 5, "mode": "standard"},
            proxies={},
        ),
    )

# Lightweight factory: `async with s3_client() as s3: ...`
def s3_client(
    *,
    endpoint_url: str = R2_ENDPOINT,
    region_name: str = R2_REGION,
    access_key_id: str = R2_ACCESS_KEY_ID,
    secret_access_key: str = R2_SECRET_ACCESS_KEY,
):
    return _session.client(
        **_client_kwargs(
            endpoint_url=endpoint_url,
            region_name=region_name,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
        )
    )
