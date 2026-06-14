import base64
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Iterable

import logging

from fastapi import HTTPException
from openai.types.responses import FileSearchToolParam, WebSearchToolParam
from openai.types.responses.tool import CodeInterpreter, ImageGeneration
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import Conversation, MessageContent
from app.api.document_helpers import touch_conversation_documents_last_used_in_search
from app.r2.methods import delete_object, put_bytes
from app.r2.client import R2_BUCKET
from app.r2.settings import Settings
from app.services.image_assets import (
    IMAGE_SOURCE_GENERATED,
    create_image_asset,
    object_prefix_for_user,
    partial_object_prefix,
    serialize_image_asset,
)
from app.redis.event_bus import RedisEventBus
from app.redis.settings import settings
from app.services.ai_service import stream_normalized_ai_response
from app.db.database import engine
from app.services.conversation_search import queue_assistant_index_refresh
from app.services.model_registry import get_image_model_provider
from app.services.subscription_check.entitlements import reserve_request, finalize_request
from app.services.subscription_check.pacing import get_image_quality_cost

logger = logging.getLogger(__name__)
IMAGE_STORAGE_ERROR_CODE = "IMAGE_STORAGE_UNAVAILABLE"
IMAGE_STORAGE_ERROR_MESSAGE = "Generated image could not be stored."


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
        model: Optional[str] = "gpt-5.4-nano",
        tool_choice: Optional[str | dict[str, Any]] = "auto",
        request_id: Optional[str] = None,
        previous_response_id: Optional[str] = None,
        previous_interaction_id: Optional[str] = None,
        chain_context_fingerprint: Optional[str] = None,
        image_entitlement_tier_id: Optional[uuid.UUID] = None,
        image_entitlement_pack_id: Optional[uuid.UUID] = None,
        fallback_history_for_openai: Optional[list] = None,
        thinking_enabled: Optional[bool] = None,
        reasoning_effort: Optional[str] = None,
):
    async with AsyncSession(engine, expire_on_commit=False) as session:
        buffers: dict[int, str] = {}
        last_ckpt: dict[int, int] = {}
        content_cache: dict[int, MessageContent] = {}
        partial_image_keys: dict[int, list[str]] = {}
        lifecycle: dict[str, Any] = {
            "text_request_finalized": False,
            "stream_failed": False,
            "last_error_message": None,
            "last_error_code": None,
        }
        assistant_message_id_str = str(assistant_message_id)

        try:
            await bus.publish(assistant_message_id_str, {"type": "start"})

            async for ev in stream_normalized_ai_response(
                    history_for_openai,
                    model,
                    instructions=instructions,
                    tool_choice=tool_choice,
                    tools=tools,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    request_id=request_id,
                    assistant_message_id=assistant_message_id,
                    previous_response_id=previous_response_id,
                    previous_interaction_id=previous_interaction_id,
                    fallback_messages=fallback_history_for_openai,
                    thinking_enabled=thinking_enabled,
                    reasoning_effort=reasoning_effort,
            ):
                if ev.get("type") not in {"image.partial", "image.ready", "response.meta"}:
                    await bus.publish(assistant_message_id_str, ev)

                await _handle_stream_event(
                    ev=ev,
                    assistant_message_id=assistant_message_id,
                    session=session,
                    request_id=request_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    tools=tools,
                    bus=bus,
                    image_entitlement_tier_id=image_entitlement_tier_id,
                    image_entitlement_pack_id=image_entitlement_pack_id,
                    buffers=buffers,
                    last_ckpt=last_ckpt,
                    content_cache=content_cache,
                    partial_image_keys=partial_image_keys,
                    lifecycle=lifecycle,
                    chain_context_fingerprint=chain_context_fingerprint,
                )

        except Exception as e:
            logger.exception(
                "Generate/publish pipeline failed request_id=%s conversation_id=%s assistant_message_id=%s",
                request_id,
                str(conversation_id),
                str(assistant_message_id),
            )
            await _cleanup_partial_images(partial_image_keys)
            error_event = {
                "type": "error",
                "error": lifecycle.get("last_error_message") or str(e),
            }
            if lifecycle.get("last_error_code"):
                error_event["code"] = lifecycle["last_error_code"]
            await bus.publish(assistant_message_id_str, error_event)
            await bus.mark_done(
                assistant_message_id_str,
                ok=False,
                error=lifecycle.get("last_error_message") or str(e),
            )
            raise
        else:
            if lifecycle.get("stream_failed"):
                await bus.mark_done(
                    assistant_message_id_str,
                    ok=False,
                    error=lifecycle.get("last_error_message") or "stream_failed",
                )
            else:
                await bus.mark_done(assistant_message_id_str, ok=True)
        finally:
            await _clear_active_stream_pointer(
                bus=bus,
                conversation_id=conversation_id,
                assistant_message_id=assistant_message_id_str,
            )



async def _handle_stream_event(
        *,
        ev: dict,
        assistant_message_id: uuid.UUID,
        session: AsyncSession,
        request_id: Optional[str],
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        tools: Optional[Iterable[FileSearchToolParam | WebSearchToolParam | CodeInterpreter | ImageGeneration]],
        bus: RedisEventBus,
        image_entitlement_tier_id: Optional[uuid.UUID],
        image_entitlement_pack_id: Optional[uuid.UUID],
        buffers: dict[int, str],
        last_ckpt: dict[int, int],
        content_cache: dict[int, MessageContent],
        partial_image_keys: dict[int, list[str]],
        lifecycle: dict[str, Any],
        chain_context_fingerprint: Optional[str],
) -> None:
    event_type = ev.get("type")

    if event_type == "part.start":
        return

    if event_type == "text.delta":
        i = ev["index"]
        buffers[i] = buffers.get(i, "") + ev["text"]
        lifecycle["assistant_text_dirty"] = True

        checkpoint_bytes = settings.CHECKPOINT_BYTES
        if len(buffers[i]) - last_ckpt.get(i, 0) >= checkpoint_bytes:
            await _upsert_text(assistant_message_id, i, buffers[i], session=session, content_cache=content_cache)
            last_ckpt[i] = len(buffers[i])
        return

    if event_type == "text.done":
        i = ev["index"]
        if i in buffers:
            await _upsert_text(assistant_message_id, i, buffers[i], session=session, content_cache=content_cache)

        if not lifecycle.get("text_request_finalized"):
            await finalize_request(session, request_id=request_id, user_id=user_id, success=True)
            lifecycle["text_request_finalized"] = True
        return

    if event_type == "image.ready":
        ordinal = ev.get("index", 0)
        prefix = await object_prefix_for_user(session, user_id, IMAGE_SOURCE_GENERATED)
        try:
            url, image_key = await upload_openai_image_to_r2_with_key(ev["data"], prefix=prefix)
        except Exception:
            lifecycle["last_error_code"] = IMAGE_STORAGE_ERROR_CODE
            lifecycle["last_error_message"] = IMAGE_STORAGE_ERROR_MESSAGE
            logger.exception(
                "Failed to persist final generated image request_id=%s conversation_id=%s assistant_message_id=%s index=%s",
                request_id,
                str(conversation_id),
                str(assistant_message_id),
                ordinal,
            )
            raise
        save_result = await save_image_url_to_db(
            url,
            ordinal,
            assistant_message_id,
            session=session,
            user_id=user_id,
            conversation_id=conversation_id,
            bucket=R2_BUCKET,
            key=image_key,
            source=IMAGE_SOURCE_GENERATED,
        )
        if isinstance(save_result, tuple):
            image_content, image_asset = save_result
        else:
            image_content, image_asset = None, None
        await bus.publish(str(assistant_message_id), {
            "type": "image.url",
            "index": ordinal,
            "url": url,
            "image_id": str(image_asset.id) if image_asset else (str(image_content.id) if image_content else None),
            "expires_at": image_asset.expires_at.isoformat(timespec="seconds") if image_asset and image_asset.expires_at else None,
            "status": image_asset.status if image_asset else "active",
            "image": serialize_image_asset(image_asset),
        })

        image_model = _extract_image_model_name(tools) or "unknown"
        image_provider = get_image_model_provider(image_model) if image_model != "unknown" else "openai"
        image_option_value = (
            (_extract_image_size(tools) or "1k")
            if image_provider == "google"
            else (_extract_image_quality(tools) or "low")
        )
        image_cost = await get_image_quality_cost(session, image_model, image_option_value)
        await update_request_ledger_image(
            session,
            request_id,
            user_id,
            ordinal,
            conversation_id,
            assistant_message_id,
            image_model,
            image_cost,
            image_entitlement_tier_id,
            image_entitlement_pack_id,
        )
        # Final image is ready; remove temporary partial images for this output index.
        await _cleanup_partial_images({ordinal: partial_image_keys.pop(ordinal, [])})
        print("SAVED THE IMAGE")
        return

    if event_type == "image.partial":
        ordinal = ev.get("index", 0)
        try:
            partial_url, partial_key = await upload_openai_image_to_r2_with_key(
                ev["data"],
                prefix=partial_object_prefix(),
                suffix=f"{ordinal}-{ev.get('partial_index', 0)}-{ev.get('sequence_number', 0)}",
            )
        except Exception:
            logger.warning(
                "Skipping partial generated image persistence failure request_id=%s conversation_id=%s assistant_message_id=%s index=%s partial_index=%s sequence_number=%s",
                request_id,
                str(conversation_id),
                str(assistant_message_id),
                ordinal,
                ev.get("partial_index", 0),
                ev.get("sequence_number", 0),
                exc_info=True,
            )
            return
        partial_image_keys.setdefault(ordinal, []).append(partial_key)
        await bus.publish(str(assistant_message_id), {
            "type": "image.partial",
            "index": ordinal,
            "format": "url",
            "url": partial_url,
            "partial_index": ev.get("partial_index", 0),
            "sequence_number": ev.get("sequence_number", 0),
        })
        await bus.publish(str(assistant_message_id), {
            "type": "image.partial_url",
            "index": ordinal,
            "url": partial_url,
            "partial_index": ev.get("partial_index", 0),
            "sequence_number": ev.get("sequence_number", 0),
        })
        return

    if event_type == "status":
        # not saving to DB
        return

    if event_type == "response.meta":
        conversation = await session.get(Conversation, conversation_id)
        if not conversation:
            return

        provider = str(ev.get("provider") or "").strip().lower()
        response_id_raw = ev.get("response_id")
        interaction_id_raw = ev.get("interaction_id")
        response_id = str(response_id_raw).strip() if response_id_raw is not None else ""
        interaction_id = str(interaction_id_raw).strip() if interaction_id_raw is not None else ""

        if provider == "google" and interaction_id:
            conversation.last_google_interaction_id = interaction_id
            conversation.google_chain_updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            conversation.google_chain_context_fingerprint = chain_context_fingerprint
            session.add(conversation)
            await session.commit()
            return

        if response_id and response_id.startswith("resp_"):
            conversation.last_openai_response_id = response_id
            conversation.openai_chain_updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            conversation.openai_chain_context_fingerprint = chain_context_fingerprint
            session.add(conversation)
            await session.commit()
        elif response_id:
            logger.warning(
                "Skipping non-canonical OpenAI response id for chaining: %s",
                response_id,
            )
        return

    if event_type in {"reasoning.summary.delta", "reasoning.summary.done"}:
        # UI-only events for frontend stream
        return

    if event_type == "file_search.used":
        if not lifecycle.get("file_search_touched"):
            await touch_conversation_documents_last_used_in_search(session, conversation_id)
            lifecycle["file_search_touched"] = True
        return

    if event_type == "done":
        # Some responses (for example image-only) may not emit text.done.
        if not lifecycle.get("text_request_finalized"):
            await finalize_request(session, request_id=request_id, user_id=user_id, success=True)
            lifecycle["text_request_finalized"] = True
        if lifecycle.get("assistant_text_dirty"):
            await queue_assistant_index_refresh(
                conversation_id=conversation_id,
                assistant_message_id=assistant_message_id,
            )
            lifecycle["assistant_text_dirty"] = False
        return

    if event_type == "error":
        lifecycle["stream_failed"] = True
        lifecycle["last_error_code"] = ev.get("code")
        lifecycle["last_error_message"] = ev.get("data") or ev.get("error") or "OpenAI stream error"
        if not lifecycle.get("text_request_finalized"):
            await finalize_request(session, request_id=request_id, user_id=user_id, success=False)
            lifecycle["text_request_finalized"] = True
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
        if isinstance(tool, dict):
            if str(tool.get("type") or "").strip().lower() == "image_generation":
                return tool.get("model")
            continue
        if isinstance(tool, ImageGeneration):
            return getattr(tool, "model", None)

    return None


def _extract_image_quality(
        tools: Optional[Iterable[FileSearchToolParam | WebSearchToolParam | CodeInterpreter | ImageGeneration]],
) -> Optional[str]:
    if not tools:
        return None

    for tool in tools:
        if isinstance(tool, dict):
            if str(tool.get("type") or "").strip().lower() == "image_generation":
                return tool.get("quality")
            continue
        if isinstance(tool, ImageGeneration):
            return getattr(tool, "quality", None)

    return None


def _extract_image_size(
        tools: Optional[Iterable[FileSearchToolParam | WebSearchToolParam | CodeInterpreter | ImageGeneration]],
) -> Optional[str]:
    if not tools:
        return None

    for tool in tools:
        if isinstance(tool, dict) and str(tool.get("type") or "").strip().lower() == "image_generation":
            return tool.get("image_size")

    return None


async def _upsert_text(message_id, ordinal, text, *, session: AsyncSession | None = None, content_cache: dict[int, MessageContent] | None = None):
    if session is None:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await _upsert_text(message_id, ordinal, text, session=session)
        return

    row = None
    if content_cache is not None and ordinal in content_cache:
        row = content_cache[ordinal]

    if not row:
        res = await session.exec(
            select(MessageContent).where(
                MessageContent.message_id == message_id,
                MessageContent.ordinal == ordinal,
                MessageContent.type == "text",
            )
        )
        row = res.first()
        if content_cache is not None and row:
            content_cache[ordinal] = row

    if row:
        row.value = text
        session.add(row)
    else:
        row = MessageContent(message_id=message_id, ordinal=ordinal, type="text", value=text)
        session.add(row)
        if content_cache is not None:
            content_cache[ordinal] = row

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
    url, _ = await upload_openai_image_to_r2_with_key(b64_png, prefix=prefix)
    return url


async def upload_openai_image_to_r2_with_key(
        b64_png: str,
        prefix: str = "gen",
        suffix: str | None = None,
) -> tuple[str, str]:
    data = base64.b64decode(b64_png)
    sha = hashlib.sha256(data).hexdigest()
    filename = f"{sha}-{suffix}.png" if suffix else f"{sha}.png"
    key = f"{prefix}/{sha[:2]}/{filename}"
    bucket, key = await put_bytes(key, data, content_type="image/png", metadata={"source": "openai"})
    return f"{Settings.R2_PUBLIC_BASE_URL}{key}", key


async def _cleanup_partial_images(partial_keys_by_index: dict[int, list[str]]) -> None:
    for keys in partial_keys_by_index.values():
        for key in keys:
            try:
                await delete_object(key)
            except Exception:
                # Best-effort cleanup for temporary objects.
                continue


async def _clear_active_stream_pointer(
    *,
    bus: RedisEventBus,
    conversation_id: uuid.UUID,
    assistant_message_id: str,
) -> None:
    # Some tests use a minimal fake bus without redis backing.
    if not hasattr(bus, "r") or bus.r is None:
        return

    key = f"conv:{conversation_id}:current"
    current = await bus.r.get(key)
    if current is None:
        return

    if isinstance(current, bytes):
        current_value = current.decode("utf-8")
    else:
        current_value = str(current)

    if current_value == assistant_message_id:
        await bus.r.delete(key)


# To use this function, we need to have a message created already
async def save_image_url_to_db(
        image_url: str,
        ordinal: int,
        message_id: uuid.UUID,
        *,
        session: AsyncSession | None = None,
        user_id: uuid.UUID | None = None,
        conversation_id: uuid.UUID | None = None,
        bucket: str | None = None,
        key: str | None = None,
        source: str = IMAGE_SOURCE_GENERATED,
) -> tuple[MessageContent, object | None]:
    if session is None:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            return await save_image_url_to_db(
                image_url,
                ordinal,
                message_id,
                session=session,
                user_id=user_id,
                conversation_id=conversation_id,
                bucket=bucket,
                key=key,
                source=source,
            )

    addition = MessageContent(message_id=message_id, ordinal=ordinal, type="image_url", value=image_url)
    session.add(addition)
    await session.flush()
    asset = None
    if user_id is not None:
        asset = await create_image_asset(
            session,
            user_id=user_id,
            conversation_id=conversation_id,
            message_content=addition,
            public_url=image_url,
            bucket=bucket,
            key=key,
            source=source,
        )
    await session.commit()
    await session.refresh(addition)
    if asset:
        await session.refresh(asset)
    return addition, asset


async def update_request_ledger_image(session: AsyncSession, request_id: str, user_id: uuid.UUID, ordinal: int,
                                     conversation_id: uuid.UUID, assistant_message_id: uuid.UUID, image_model: str,
                                     cost: float, tier_id: Optional[uuid.UUID] = None,
                                     usage_pack_id: Optional[uuid.UUID] = None):
    img_req_id = f"{request_id}:img:{ordinal}"
    await reserve_request(
        session,
        user_id=user_id,
        conversation_id=conversation_id,
        assistant_message_id=assistant_message_id,
        request_id=img_req_id,
        model_name=image_model,
        feature="image",
        cost=cost,
        tool_choice="image_generation",
        tier_id=tier_id,
        usage_pack_id=usage_pack_id,
    )
    await finalize_request(session, request_id=img_req_id, user_id=user_id, success=True)
