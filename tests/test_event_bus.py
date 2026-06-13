import pytest

from app.redis.event_bus import RedisEventBus


class FakeRedis:
    def __init__(self):
        self.xadd_calls = []
        self.expire_calls = []

    async def xadd(self, key, event, maxlen=None, approximate=None):
        self.xadd_calls.append(
            {
                "key": key,
                "event": event,
                "maxlen": maxlen,
                "approximate": approximate,
            }
        )
        return "1-0"

    async def expire(self, key, ttl):
        self.expire_calls.append((key, ttl))


@pytest.mark.asyncio
async def test_publish_normalizes_nested_payloads_and_omits_none():
    redis = FakeRedis()
    bus = RedisEventBus(redis)

    await bus.publish(
        "mid-123",
        {
            "type": "image.url",
            "index": 0,
            "url": "https://cdn.example/image.png",
            "image": {"id": "asset-1", "status": "active"},
            "partials": ["a", "b"],
            "expires_at": None,
            "raw": b"bytes-value",
        },
    )

    assert len(redis.xadd_calls) == 1
    published = redis.xadd_calls[0]["event"]
    assert published == {
        "type": "image.url",
        "index": 0,
        "url": "https://cdn.example/image.png",
        "image": '{"id":"asset-1","status":"active"}',
        "partials": '["a","b"]',
        "raw": "bytes-value",
    }
    assert redis.expire_calls


@pytest.mark.asyncio
async def test_mark_done_uses_publish_safe_payload():
    redis = FakeRedis()
    bus = RedisEventBus(redis)

    await bus.mark_done("mid-456", ok=False, error=None)

    assert redis.xadd_calls[0]["event"] == {
        "type": "error",
        "error": "unknown",
    }
