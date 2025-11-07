from datetime import datetime, timezone

from sqlmodel import select

from app.db.models import MessageContent
from app.redis.settings import settings


async def _choose_link_for_message(session, bus, conversation_id, assistant_message_id, ledger_created_at):
    # 1) If reply already exists in DB => GET endpoint
    exists_row = (await session.exec(
        select(MessageContent.id).where(MessageContent.message_id == assistant_message_id)
    )).first()
    if exists_row:
        return {
            "message_id": str(assistant_message_id),
            "stream_url": None,
            "messages_url": f"/api/v1/conversations/{conversation_id}/messages"
        }

    # 2) If older than stream TTL or stream is gone => GET endpoint
    too_old = (datetime.now(timezone.utc).timestamp()
               - ledger_created_at.replace(tzinfo=timezone.utc).timestamp()) > settings.STREAM_TTL_SECONDS
    stream_exists = await bus.exists(str(assistant_message_id))  # redis stream key check
    if too_old or not stream_exists:
        return {
            "message_id": str(assistant_message_id),
            "stream_url": None,
            "messages_url": f"/api/v1/conversations/{conversation_id}/messages"
        }

    # 3) Otherwise, the stream is viable
    return {
        "message_id": str(assistant_message_id),
        "stream_url": f"/api/v1/conversations/{conversation_id}/messages/{assistant_message_id}/stream",
        "messages_url": None
    }