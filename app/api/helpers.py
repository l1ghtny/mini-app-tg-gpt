import base64
import hashlib
import uuid
from pprint import pprint
from typing import Optional, Iterable

from fastapi import HTTPException
from openai.types.beta import FileSearchToolParam
from openai.types.responses import WebSearchToolParam
from openai.types.responses.tool import CodeInterpreter, ImageGeneration
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import Conversation, MessageContent
from app.r2.methods import put_bytes
from app.r2.settings import Settings
from app.redis.event_bus import RedisEventBus
from app.redis.settings import settings
from app.services.openai_service import stream_normalized_openai_response
from app.db.database import engine
from app.services.subscription_check.entitlements import reserve_request, finalize_request


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
        tools: Optional[Iterable[FileSearchToolParam | WebSearchToolParam | CodeInterpreter | ImageGeneration]],
        instructions: Optional[str] = None,
        model: Optional[str] = "gpt-5-nano",
        tool_choice: Optional[str] = "auto",
        request_id: Optional[str] = None,
):
    async with AsyncSession(engine, expire_on_commit=False) as session:
        buffers: dict[int, str] = {}
        last_ckpt: dict[int, int] = {}
        assistant_message_id_str = str(assistant_message_id)

        try:
            await bus.publish(assistant_message_id_str, {"type": "start"})

            async for ev in stream_normalized_openai_response(
                    history_for_openai,
                    model,
                    instructions=instructions,
                    tool_choice=tool_choice,
                    tools=tools,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    request_id=request_id,
            ):
                await bus.publish(assistant_message_id_str, ev)

                await _handle_stream_event(
                    ev=ev,
                    assistant_message_id=assistant_message_id,
                    session=session,
                    request_id=request_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    tools=tools,
                    buffers=buffers,
                    last_ckpt=last_ckpt,
                )

        except Exception as e:
            await bus.publish(assistant_message_id_str, {"type": "error", "error": str(e)})
            await bus.mark_done(assistant_message_id_str, ok=False, error=str(e))
            raise
        else:
            await bus.mark_done(assistant_message_id_str, ok=True)


async def _handle_stream_event(
        *,
        ev: dict,
        assistant_message_id: uuid.UUID,
        session: AsyncSession,
        request_id: Optional[str],
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        tools: Optional[Iterable[FileSearchToolParam | WebSearchToolParam | CodeInterpreter | ImageGeneration]],
        buffers: dict[int, str],
        last_ckpt: dict[int, int],
) -> None:
    event_type = ev.get("type")

    if event_type == "part.start":
        return

    if event_type == "text.delta":
        i = ev["index"]
        buffers[i] = buffers.get(i, "") + ev["text"]

        checkpoint_bytes = settings.CHECKPOINT_BYTES
        if len(buffers[i]) - last_ckpt.get(i, 0) >= checkpoint_bytes:
            await _upsert_text(assistant_message_id, i, buffers[i])
            last_ckpt[i] = len(buffers[i])
        return

    if event_type == "text.done":
        i = ev["index"]
        if i in buffers:
            await _upsert_text(assistant_message_id, i, buffers[i])

        await finalize_request(session, request_id=request_id, user_id=user_id, success=True)
        print("UPDATED REQUEST LEDGER")
        return

    if event_type == "image.ready":
        ordinal = ev.get("index", 0)
        url = await upload_openai_image_to_r2(ev["data"], prefix="gen")
        await save_image_url_to_db(url, ordinal, assistant_message_id)

        image_model = _extract_image_model_name(tools) or "unknown"
        await update_request_ledger_image(
            session,
            request_id,
            user_id,
            ordinal,
            conversation_id,
            assistant_message_id,
            image_model,
        )
        print("SAVED THE IMAGE")
        return

    if event_type == "status":
        # not saving to DB
        return

    if event_type == "error":
        await finalize_request(session, request_id=request_id, user_id=user_id, success=False)
        # optional: mark message status in DB
        return


def _extract_image_model_name(
        tools: Optional[Iterable[FileSearchToolParam | WebSearchToolParam | CodeInterpreter | ImageGeneration]],
) -> Optional[str]:
    """
    Safely extract ImageGeneration model name from an Iterable of tools.

    Avoids `tools[ImageGeneration].model` which fails because `tools` isn't a dict.
    """
    if not tools:
        return None

    for tool in tools:
        if isinstance(tool, ImageGeneration):
            return getattr(tool, "model", None)

    return None


async def _upsert_text(message_id, ordinal, text):
    async with AsyncSession(engine, expire_on_commit=False) as session:
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
    return f"{Settings.R2_PUBLIC_BASE_URL}{bucket}/{key}"


# To use this function, we need to have a message created already
async def save_image_url_to_db(image_url: str, ordinal: int, message_id: uuid.UUID):
    async with AsyncSession(engine, expire_on_commit=False) as session:
        addition = MessageContent(message_id=message_id, ordinal=ordinal, type="image_url", value=image_url)
        session.add(addition)
        await session.commit()
        await session.refresh(addition)


async def update_request_ledger_image(session: AsyncSession, request_id: str, user_id: uuid.UUID, ordinal: int,
                                     conversation_id: uuid.UUID, assistant_message_id: uuid.UUID, image_model: str):
    img_req_id = f"{request_id}:img:{ordinal}"
    await reserve_request(
        session,
        user_id=user_id,
        conversation_id=conversation_id,
        assistant_message_id=assistant_message_id,
        request_id=img_req_id,
        model_name=image_model,
        feature="image",
        tool_choice="image_generation",
    )
    await finalize_request(session, request_id=img_req_id, user_id=user_id, success=True)