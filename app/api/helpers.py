import uuid
from typing import Optional

from fastapi import HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import Conversation, MessageContent
from app.redis.event_bus import RedisEventBus
from app.redis.settings import settings
from app.services.streaming.events import coalesced_openai_events
from app.db.database import engine


async def load_conversation(session: AsyncSession, conversation_id: uuid.UUID) -> Conversation | None:
    result = await session.exec(select(Conversation).where(Conversation.id == conversation_id))
    conv = result.first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


async def generate_and_publish(
    conversation_id: uuid.UUID,
    assistant_message_id: uuid.UUID,
    user_id: uuid.UUID,
    history_for_openai: list,
    bus: RedisEventBus,
    instructions: Optional[str] = None,
    model: Optional[str] = 'gpt-5-nano',
    tool_choice: Optional[str] = 'auto',

):
    async with AsyncSession(engine, expire_on_commit=False) as session:
        buf = []
        bytes_since_ckpt = 0
        try:
            async for evt in coalesced_openai_events(history_for_openai, model=model, tool_choice=tool_choice, instructions=instructions, user_id=user_id, conversation_id=conversation_id):
                t = evt.get("type")
                if t == "start":
                    await bus.publish(str(assistant_message_id), {"type": "start"})
                elif t == "delta":
                    text = evt["text"]
                    buf.append(text)
                    bytes_since_ckpt += len(text)
                    await bus.publish(str(assistant_message_id), {"type": "delta", "text": text})
                    if bytes_since_ckpt >= settings.CHECKPOINT_BYTES:
                        await _upsert_text_checkpoint(session, assistant_message_id, "".join(buf))
                        bytes_since_ckpt = 0
                elif t == "done":
                    # Final write
                    if buf:
                        await _upsert_text_checkpoint(session, assistant_message_id, "".join(buf))
                    await bus.mark_done(str(assistant_message_id), ok=True)
        except Exception as e:
            await bus.publish(str(assistant_message_id), {"type": "error", "error": str(e)})
            await bus.mark_done(str(assistant_message_id), ok=False, error=str(e))

async def _upsert_text_checkpoint(session: AsyncSession, msg_id: uuid.UUID, text: str):
    # Store or update the assistant text as a single content row.
    # If you want “streaming transcript”, you can update same row; elsewhere you can keep checkpoints in a temp table.
    existing = await session.exec(
        select(MessageContent).where(
            MessageContent.message_id == msg_id,
            MessageContent.type == "text"
        )
    )
    message_content = existing.first()
    if message_content:
        message_content.value = text
    else:
        message_content = MessageContent(message_id=msg_id, type="text", value=text)
        session.add(message_content)
    await session.commit()


async def fetch_assistant_text(session: AsyncSession, message_id: uuid.UUID) -> Optional[str]:
    res = await session.exec(
        select(MessageContent).where(
            MessageContent.message_id == message_id,
            MessageContent.type == "text"
        )
    )
    message_content = res.first()
    return message_content.value if message_content else None