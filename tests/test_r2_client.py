import os

os.environ.setdefault("R2_BUCKET", "test-bucket")
os.environ.setdefault("R2_ENDPOINT", "https://example.r2.cloudflarestorage.com")
os.environ.setdefault("R2_REGION", "auto")
os.environ.setdefault("R2_ACCESS_KEY_ID", "test-access-key")
os.environ.setdefault("R2_SECRET_ACCESS_KEY", "test-secret-key")

from app.r2.client import _normalize_r2_endpoint


def test_normalize_r2_endpoint_strips_bucket_path():
    assert (
        _normalize_r2_endpoint("https://3976b304dd7e75b248d867d320740478.r2.cloudflarestorage.com/tg-bot-images")
        == "https://3976b304dd7e75b248d867d320740478.r2.cloudflarestorage.com"
    )


def test_normalize_r2_endpoint_keeps_origin_only_endpoint():
    assert (
        _normalize_r2_endpoint("https://3976b304dd7e75b248d867d320740478.r2.cloudflarestorage.com")
        == "https://3976b304dd7e75b248d867d320740478.r2.cloudflarestorage.com"
    )
