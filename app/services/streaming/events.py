import asyncio
from typing import AsyncIterator, Optional
from uuid import UUID

from app.redis.settings import settings
from app.services.openai_service import stream_openai_response


async def coalesced_openai_events(history: list, model: str, user_id, conversation_id: UUID, tool_choice: Optional[str] = None, instructions: Optional[str] = None, ) -> AsyncIterator[dict]:
    """Coalesce small token bursts into ~COALESCE_MS frames and output 'delta' events."""
    yield {"type": "start"}
    buf = []
    last_flush = asyncio.get_running_loop().time()

    async for tok in stream_openai_response(history, model=model, tool_choice=tool_choice, instructions=instructions, user_id=user_id, conversation_id=conversation_id):
        if tok:
            buf.append(tok)
        now = asyncio.get_running_loop().time()
        if (now - last_flush) * 1000 >= settings.COALESCE_MS:
            if buf:
                yield {"type": "delta", "text": "".join(buf)}
                buf.clear()
            last_flush = now

    if buf:
        yield {"type": "delta", "text": "".join(buf)}
    yield {"type": "done"}
