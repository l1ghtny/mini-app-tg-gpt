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