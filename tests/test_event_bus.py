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


class FakeReadRedis:
    def __init__(self, event):
        self.event = event

    async def xread(self, streams, block=None, count=None):
        key = next(iter(streams))
        return [(key, [("1-0", self.event)])]


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
            "truncated": False,
            "complete": True,
            "metadata": {"visible": True},
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
        "truncated": 0,
        "complete": 1,
        "metadata": '{"visible":true}',
        "raw": "bytes-value",
    }
    assert type(published["truncated"]) is int
    assert type(published["complete"]) is int
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


@pytest.mark.asyncio
async def test_read_restores_nested_activity_without_decoding_text():
    bus = RedisEventBus(
        FakeReadRedis(
            {
                "type": "activity.upsert",
                "activity": '{"event_key":"web-search-1","detail":{"sources":[{"url":"https://example.com"}]}}',
                "text": '{"keep":"as text"}',
            }
        )
    )

    event_id, event = await anext(bus.read("mid-789"))

    assert event_id == "1-0"
    assert event["activity"] == {
        "event_key": "web-search-1",
        "detail": {"sources": [{"url": "https://example.com"}]},
    }
    assert event["text"] == '{"keep":"as text"}'
