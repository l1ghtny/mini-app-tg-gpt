import os

os.environ.setdefault("R2_BUCKET", "test-bucket")
os.environ.setdefault("R2_ENDPOINT", "https://example.r2.cloudflarestorage.com/test-bucket")
os.environ.setdefault("R2_REGION", "auto")
os.environ.setdefault("R2_ACCESS_KEY_ID", "test-access-key")
os.environ.setdefault("R2_SECRET_ACCESS_KEY", "test-secret-key")

from app.r2.client import _client_kwargs


def test_r2_client_kwargs_disable_proxy_inheritance():
    kwargs = _client_kwargs()
    assert kwargs["config"].proxies == {}
