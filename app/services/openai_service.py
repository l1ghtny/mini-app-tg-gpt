import asyncio
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, Iterable, List, Literal, Optional

from openai import AsyncOpenAI, AuthenticationError, NotFoundError
from openai.types.responses import (
    FileSearchToolParam,
    ToolChoiceAllowedParam,
    ToolChoiceTypesParam,
    WebSearchToolParam,
)
from openai.types.responses.tool import CodeInterpreter, WebSearchTool
from openai.types.responses.tool_param import ImageGeneration
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings as main_settings
from app.db.database import engine
from app.redis.settings import settings
from app.schemas.chat import Message
from app.services.background.save_openai_usage import log_usage

STYLE_GUIDE = (
    "Format replies in Markdown:\n"
    "- Use proper headings for sections (##, ###).\n"
    "- Use bullet lists with '-' and numbered lists with '1.' (not '1)')\n"
    "- Use fenced code blocks for code.\n"
    "- Use standard [text](url) links.\n"
    "Only use headings, bullet lists, and others when it is applicable, don't use big headings for short messages"
)

client = AsyncOpenAI()
logger = main_settings.custom_logger

default_tools = [
    ImageGeneration(type="image_generation", model="gpt-image-1-mini", quality="medium", partial_images=2),
    WebSearchTool(type="web_search"),
]


def _is_openai_image_download_timeout(exc: Exception) -> bool:
    msg = str(exc)
    return ("Timeout while downloading" in msg) and (
        ("param" in msg and "'url'" in msg) or ("param': 'url'" in msg)
    )


async def _retry_delay_s(attempt: int, base: float = 0.8, cap: float = 8.0) -> float:
    return min(cap, base * (2 ** attempt)) + random.random() * 0.25


@dataclass
class UsageTracker:
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    web_search_calls: int = 0
    images_generated: int = 0

    def apply_completed_event(self, event: Any) -> None:
        usage = getattr(getattr(event, "response", None), "usage", None)
        if not usage:
            return

        self.input_tokens = usage.input_tokens or self.input_tokens
        self.output_tokens = usage.output_tokens or self.output_tokens

        output_tokens_details = getattr(usage, "output_tokens_details", None)
        if output_tokens_details:
            self.reasoning_tokens = (
                getattr(output_tokens_details, "reasoning_tokens", None) or self.reasoning_tokens
            )


@dataclass
class StreamState:
    text_buf: Dict[int, str] = field(default_factory=dict)
    last_flush: Dict[int, float] = field(default_factory=dict)
    seen_text_part_started: Dict[int, bool] = field(default_factory=dict)
    seen_image_part_started: Dict[int, bool] = field(default_factory=dict)
    reasoning_started: bool = False


def _build_status_event(
    *,
    stage: str,
    phase: str,
    label: str,
    source_event: str,
    event: Any,
    index: Optional[int] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "type": "status",
        "stage": stage,  # backwards compatible field
        "phase": phase,
        "label": label,
        "source_event": source_event,
        "sequence_number": getattr(event, "sequence_number", None),
        "ts": int(time.time() * 1000),
    }
    if index is not None:
        payload["index"] = index
    return payload


def _ensure_text_part_started(state: StreamState, content_index: int) -> list[Dict[str, Any]]:
    if state.seen_text_part_started.get(content_index):
        return []
    state.seen_text_part_started[content_index] = True
    state.text_buf.setdefault(content_index, "")
    state.last_flush.setdefault(content_index, 0)
    return [{"type": "part.start", "index": content_index, "content_type": "text"}]


def _ensure_image_part_started(state: StreamState, output_index: int) -> list[Dict[str, Any]]:
    if state.seen_image_part_started.get(output_index):
        return []
    state.seen_image_part_started[output_index] = True
    return [{"type": "part.start", "index": output_index, "content_type": "image"}]


async def _flush_text_delta(
    *,
    state: StreamState,
    content_index: int,
    force: bool = False,
) -> list[Dict[str, Any]]:
    if not state.text_buf.get(content_index):
        return []

    now = asyncio.get_running_loop().time()
    if force or (now - state.last_flush.get(content_index, 0)) * 1000 >= settings.COALESCE_MS:
        out = [{"type": "text.delta", "index": content_index, "text": state.text_buf[content_index]}]
        state.text_buf[content_index] = ""
        state.last_flush[content_index] = now
        return out
    return []


async def _map_openai_event(
    *,
    event: Any,
    state: StreamState,
    usage: UsageTracker,
) -> list[Dict[str, Any]]:
    et = event.type
    out: list[Dict[str, Any]] = []

    if et == "response.created":
        out.append(
            _build_status_event(
                stage="queued",
                phase="response.created",
                label="Queued",
                source_event=et,
                event=event,
            )
        )
        return out

    if et == "response.in_progress":
        out.append(
            _build_status_event(
                stage="model.in_progress",
                phase="model.in_progress",
                label="Model is processing",
                source_event=et,
                event=event,
            )
        )
        return out

    if et == "response.output_item.added" and getattr(event, "item", None) and event.item.type == "reasoning":
        state.reasoning_started = True
        out.append(
            _build_status_event(
                stage="thinking",
                phase="reasoning.in_progress",
                label="Thinking",
                source_event=et,
                event=event,
            )
        )
        return out

    if et == "response.reasoning_summary_text.delta":
        if not state.reasoning_started:
            out.append(
                _build_status_event(
                    stage="thinking",
                    phase="reasoning.in_progress",
                    label="Thinking",
                    source_event=et,
                    event=event,
                )
            )
            state.reasoning_started = True
        out.append(
            {
                "type": "reasoning.summary.delta",
                "delta": event.delta,
                "output_index": event.output_index,
                "summary_index": event.summary_index,
                "item_id": event.item_id,
                "sequence_number": event.sequence_number,
            }
        )
        return out

    if et == "response.reasoning_summary_text.done":
        out.append(
            {
                "type": "reasoning.summary.done",
                "text": event.text,
                "output_index": event.output_index,
                "summary_index": event.summary_index,
                "item_id": event.item_id,
                "sequence_number": event.sequence_number,
            }
        )
        out.append(
            _build_status_event(
                stage="thinking.done",
                phase="reasoning.completed",
                label="Thinking complete",
                source_event=et,
                event=event,
            )
        )
        return out

    if et == "response.content_part.added":
        content_index = event.content_index
        part = event.part
        if getattr(part, "type", None) in ("output_text", "text", "output_text_delta"):
            out.extend(_ensure_text_part_started(state, content_index))
        return out

    if et == "response.output_text.delta":
        content_index = event.content_index
        out.extend(_ensure_text_part_started(state, content_index))
        state.text_buf[content_index] += event.delta
        out.extend(await _flush_text_delta(state=state, content_index=content_index))
        return out

    if et == "response.output_text.done":
        content_index = event.content_index
        out.extend(await _flush_text_delta(state=state, content_index=content_index, force=True))
        out.append({"type": "text.done", "index": content_index})
        return out

    if et in ("response.web_search_call.in_progress", "response.web_search_call.searching"):
        out.append(
            _build_status_event(
                stage="web_search.in_progress",
                phase="tool.web_search.searching",
                label="Searching the web",
                source_event=et,
                event=event,
            )
        )
        return out

    if et == "response.web_search_call.completed":
        usage.web_search_calls += 1
        out.append(
            _build_status_event(
                stage="web_search.completed",
                phase="tool.web_search.completed",
                label="Web search complete",
                source_event=et,
                event=event,
            )
        )
        return out

    if et == "response.output_item.done" and getattr(event, "item", None) and event.item.type == "image_generation_call":
        if getattr(event.item, "result", None):
            usage.images_generated += 1
            out.extend(_ensure_image_part_started(state, event.output_index))
            out.append(
                {
                    "type": "image.ready",
                    "index": event.output_index,
                    "format": "b64",
                    "data": event.item.result,
                }
            )
            out.append(
                _build_status_event(
                    stage="image_generation.completed",
                    phase="tool.image_generation.completed",
                    label="Image generated",
                    source_event=et,
                    event=event,
                    index=event.output_index,
                )
            )
        return out

    if et in ("response.image_generation_call.generating", "response.image_generation_call.in_progress"):
        output_index = getattr(event, "output_index", 0)
        out.extend(_ensure_image_part_started(state, output_index))
        out.append(
            _build_status_event(
                stage="image_generation.in_progress",
                phase="tool.image_generation.in_progress",
                label="Generating image",
                source_event=et,
                event=event,
                index=output_index,
            )
        )
        return out

    if et == "response.image_generation_call.partial_image":
        output_index = event.output_index
        out.extend(_ensure_image_part_started(state, output_index))
        out.append(
            {
                "type": "image.partial",
                "index": output_index,
                "format": "b64",
                "data": event.partial_image_b64,
                "partial_index": event.partial_image_index,
                "sequence_number": event.sequence_number,
            }
        )
        return out

    if et in ("response.completed", "response.completed.successfully"):
        usage.apply_completed_event(event)
        for content_index, buf in list(state.text_buf.items()):
            if buf:
                out.append({"type": "text.delta", "index": content_index, "text": buf})
                out.append({"type": "text.done", "index": content_index})
                state.text_buf[content_index] = ""
        out.append(
            _build_status_event(
                stage="completed",
                phase="response.completed",
                label="Completed",
                source_event=et,
                event=event,
            )
        )
        out.append({"type": "done"})
        return out

    if et in ("response.failed", "response.incomplete", "response.error"):
        out.append(
            _build_status_event(
                stage="error",
                phase=et,
                label="Generation failed",
                source_event=et,
                event=event,
            )
        )
        out.append({"type": "error", "data": f"OpenAI stream event: {et}"})
        return out

    return out


async def stream_normalized_openai_response(
    messages: List["Message"],
    model: Optional[str] = "gpt-5-nano",
    *,
    instructions: Optional[str] = "You are a helpful assistant.",
    tool_choice: Literal["none", "auto", "required"] | ToolChoiceAllowedParam | ToolChoiceTypesParam = "auto",
    tools: Optional[Iterable[FileSearchToolParam | WebSearchToolParam | CodeInterpreter | ImageGeneration]] = None,
    user_id: Optional[uuid.UUID] = None,
    conversation_id: Optional[uuid.UUID] = None,
    request_id: Optional[str] = None,
    reasoning_summary: Optional[Literal["auto", "concise", "detailed"]] = "concise",
) -> AsyncGenerator[Dict[str, Any], None]:
    if tools is None:
        tools = default_tools

    corr_id = request_id or str(uuid.uuid4())
    usage = UsageTracker()
    state = StreamState()

    try:
        response = None
        max_openai_retries = 3
        for attempt in range(max_openai_retries):
            try:
                create_kwargs: Dict[str, Any] = {
                    "model": model,
                    "tools": tools,
                    "tool_choice": tool_choice,
                    "instructions": (instructions or "") + STYLE_GUIDE,
                    "input": messages,
                    "stream": True,
                    "service_tier": "default",
                }
                if reasoning_summary:
                    create_kwargs["reasoning"] = {"summary": reasoning_summary}
                response = await client.responses.create(**create_kwargs)
                break
            except Exception as exc:
                if _is_openai_image_download_timeout(exc) and attempt < max_openai_retries - 1:
                    await asyncio.sleep(await _retry_delay_s(attempt))
                    continue
                raise

        try:
            async for event in response:
                mapped_events = await _map_openai_event(event=event, state=state, usage=usage)
                for mapped in mapped_events:
                    yield mapped
        except Exception as e:
            yield {"type": "error", "data": str(e)}
            raise

        async with AsyncSession(engine, expire_on_commit=False) as session:
            await log_usage(
                session,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=corr_id,
                provider="openai",
                model_name=model,
                status="success",
                error_message=None,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                reasoning_tokens=usage.reasoning_tokens,
                web_search_calls=usage.web_search_calls,
                images_generated=usage.images_generated,
            )

    except AuthenticationError:
        yield {"type": "error", "data": "OpenAI authentication failed. Check API key."}
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await log_usage(
                session,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=corr_id,
                provider="openai",
                model_name=model,
                status="error",
                error_message="authentication_error",
                input_tokens=0,
                output_tokens=0,
                reasoning_tokens=0,
                web_search_calls=0,
                images_generated=0,
            )
    except NotFoundError:
        yield {"type": "error", "data": "Model not found. Please check the model name."}
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await log_usage(
                session,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=corr_id,
                provider="openai",
                model_name=model,
                status="error",
                error_message="model_not_found",
                input_tokens=0,
                output_tokens=0,
                reasoning_tokens=0,
                web_search_calls=0,
                images_generated=0,
            )


async def generate_conversation_title(first_message: str) -> str:
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert at creating short, concise titles. Summarize the user's message in 5 words or less. Do not use quotation marks or punctuation.",
                },
                {"role": "user", "content": first_message},
            ],
            temperature=0,
            max_tokens=20,
        )
        logger.info("Generated conversation title")
        title = response.choices[0].message.content.strip().strip('"').strip(".")
        return title if title else "New Chat"
    except Exception as e:
        print(f"Error generating title: {e}")
        return "New Chat"
