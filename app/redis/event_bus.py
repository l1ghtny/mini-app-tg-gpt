from typing import AsyncIterator, Optional
from redis.asyncio import Redis
from .settings import settings


class RedisEventBus:
    def __init__(self, redis: Redis):
        self.r = redis

    @staticmethod
    def key_for_message(mid: str) -> str:
        return f"msg:{mid}"

    async def publish(self, mid: str, event: dict) -> str:
        """Append an event to the stream; returns Redis stream entry ID."""
        key = self.key_for_message(mid)
        entry_id = await self.r.xadd(
            key, event, maxlen=settings.STREAM_MAXLEN, approximate=True
        )
        # refresh TTL on each write so short streams don’t expire mid-generation
        await self.r.expire(key, settings.STREAM_TTL_SECONDS)
        return entry_id

    async def mark_done(self, mid: str, ok: bool = True, error: Optional[str] = None):
        evt = {"type": "done"} if ok else {"type": "error", "error": error or "unknown"}
        await self.publish(mid, evt)

    async def read(self, mid: str, last_id: Optional[str] = None) -> AsyncIterator[tuple[str, dict]]:
        """Yield (message_id, event_map) from last_id (or from '0-0')."""
        key = self.key_for_message(mid)
        cursor = last_id or "0-0"
        while True:
            items = await self.r.xread({key: cursor}, block=15000, count=50)
            if not items:
                continue
            _, messages = items[0]
            for msg_id, fields in messages:
                cursor = msg_id
                yield msg_id, fields

    async def exists(self, mid: str) -> bool:
        return await self.r.exists(self.key_for_message(mid)) > 0
