import os

os.environ.setdefault("R2_BUCKET", "test-bucket")
os.environ.setdefault("R2_ENDPOINT", "https://example.r2.cloudflarestorage.com/test-bucket")
os.environ.setdefault("R2_REGION", "auto")
os.environ.setdefault("R2_ACCESS_KEY_ID", "test-access-key")
os.environ.setdefault("R2_SECRET_ACCESS_KEY", "test-secret-key")

from app.r2 import client as r2_client
from app.r2.client import _client_kwargs


def test_r2_client_kwargs_disable_proxy_inheritance():
    kwargs = _client_kwargs()
    assert kwargs["config"].proxies == {}


def test_r2_client_can_isolate_a_fresh_session(monkeypatch):
    class FakeSession:
        def __init__(self, marker):
            self.marker = marker

        def client(self, **_):
            return self.marker

    shared_session = FakeSession("shared")
    monkeypatch.setattr(r2_client, "_session", shared_session)
    monkeypatch.setattr(
        r2_client.aioboto3,
        "Session",
        lambda: FakeSession("fresh"),
    )

    assert r2_client.s3_client() == "shared"
    assert r2_client.s3_client(fresh_session=True) == "fresh"
