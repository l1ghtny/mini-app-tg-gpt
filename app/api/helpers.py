import base64
import hashlib
import uuid
from typing import Optional

from fastapi import HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import Conversation, MessageContent
from app.r2.methods import put_bytes
from app.r2.settings import Settings
from app.redis.event_bus import RedisEventBus
from app.redis.settings import settings
from app.services.openai_service import stream_normalized_openai_response
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
        buffers: dict[int, str] = {}
        last_ckpt: dict[int, int] = {}

        try:
            # Optional: publish a global "start"
            await bus.publish(str(assistant_message_id), {"type": "start"})

            async for ev in stream_normalized_openai_response(
                    history_for_openai, model,
                    instructions=instructions,
                    tool_choice=tool_choice,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    request_id=None,
            ):
                await bus.publish(str(assistant_message_id), ev)

                t = ev["type"]

                if t == "part.start":
                    # You can pre-create row if you like; not required.

                    pass

                elif t == "text.delta":
                    i = ev["index"]
                    txt = ev["text"]
                    buffers[i] = buffers.get(i, "") + txt
                    if len(buffers[i]) - last_ckpt.get(i, 0) >= settings.CHECKPOINT_BYTES:
                        await _upsert_text(session, assistant_message_id, i, buffers[i])
                        last_ckpt[i] = len(buffers[i])

                elif t == "text.done":
                    i = ev["index"]
                    if i in buffers:
                        await _upsert_text(session, assistant_message_id, i, buffers[i])

                elif t == "image.ready":
                    ordinal = ev.get("index", 0)
                    url = await upload_openai_image_to_r2(ev["data"])
                    await save_image_url_to_db(url, ordinal, assistant_message_id, session)

                elif t == "status":
                    # not saving to DB
                    pass

                elif t in ("done", "error"):
                    # optional: mark message status in DB
                    pass

        except Exception as e:
            await bus.publish(str(assistant_message_id), {"type": "error", "error": str(e)})
            await bus.mark_done(str(assistant_message_id), ok=False, error=str(e))
            raise e
        else:
            await bus.mark_done(str(assistant_message_id), ok=True)

async def _upsert_text(session, message_id, ordinal, text):
    res = await session.exec(
        select(MessageContent).where(
            MessageContent.message_id == message_id,
            MessageContent.ordinal == ordinal,
            MessageContent.type == "text",
        )
    )
    row = res.first()
    if row:
        row.value = text
    else:
        row = MessageContent(message_id=message_id, ordinal=ordinal, type="text", value=text)
        session.add(row)
    await session.commit()

async def _upsert_rich(session, message_id, ordinal, type_, data: dict, value: str):
    res = await session.exec(
        select(MessageContent).where(
            MessageContent.message_id == message_id,
            MessageContent.ordinal == ordinal,
            MessageContent.type == type_,
        )
    )
    row = res.first()
    if row:
        row.data = data
    else:
        row = MessageContent(message_id=message_id, ordinal=ordinal, type=type_, data=data, value=value)
        session.add(row)
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


async def upload_openai_image_to_r2(b64_png: str, prefix: str = "gen"):
    data = base64.b64decode(b64_png)
    sha = hashlib.sha256(data).hexdigest()
    key = f"{prefix}/{sha[:2]}/{sha}.png"
    bucket, key = await put_bytes(key, data, content_type="image/png", metadata={"source": "openai"})
    return f'{Settings.R2_PUBLIC_BASE_URL}{bucket}/{key}'


# To use this function, we need to have a message created already
async def save_image_url_to_db(image_url: str, ordinal: int, message_id: uuid.UUID, session: AsyncSession):
        addition = MessageContent(message_id=message_id, ordinal=ordinal, type="image_url", value=image_url)
        session.add(addition)
        await session.commit()