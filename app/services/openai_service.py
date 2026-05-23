import asyncio
import json
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, Iterable, List, Literal, Optional

from openai import APIError, AsyncOpenAI, AuthenticationError, NotFoundError
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
from app.core.metrics import track_event, track_internal_event
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

SUMMARY_PROMPT = (
    "You compress chat history for long-running assistants. "
    "Return a compact, factual summary that preserves user preferences, constraints, decisions, "
    "open tasks, and unresolved questions. "
    "Do not add facts that are not present in the source. "
    "Use plain text with short bullet lines when useful. "
    "Keep it concise."
)

TITLE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
    },
    "required": ["title"],
    "additionalProperties": False,
}

SUMMARY_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "open_tasks": {"type": "array", "items": {"type": "string"}},
        "constraints": {"type": "array", "items": {"type": "string"}},
        "decisions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary"],
    "additionalProperties": False,
}

client = AsyncOpenAI()
logger = main_settings.custom_logger

default_tools = [
    ImageGeneration(type="image_generation", model="gpt-image-1-mini", quality="medium", partial_images=2),
    WebSearchTool(type="web_search"),
]

OPENAI_UPSTREAM_ERROR_CODE = "OPENAI_UPSTREAM_UNAVAILABLE"
OPENAI_UPSTREAM_USER_MESSAGE = "Sorry, OpenAI has some issues on their end. Please try again in a moment."


def _is_openai_image_download_timeout(exc: Exception) -> bool:
    msg = str(exc)
    return ("Timeout while downloading" in msg) and (
        ("param" in msg and "'url'" in msg) or ("param': 'url'" in msg)
    )


def _is_retryable_openai_exception(exc: Exception) -> bool:
    if _is_openai_image_download_timeout(exc):
        return True

    if isinstance(exc, APIError):
        status_code = getattr(exc, "status_code", None)
        if status_code in (408, 409, 429, 500, 502, 503, 504):
            return True
        # APIError without explicit status is often transient in streaming paths
        return True

    msg = str(exc).lower()
    transient_markers = (
        "timeout",
        "temporarily unavailable",
        "service unavailable",
        "connection reset",
        "connection aborted",
        "rate limit",
    )
    return any(marker in msg for marker in transient_markers)


def _extract_openai_request_id(exc: Exception) -> str | None:
    request_id = getattr(exc, "request_id", None)
    if isinstance(request_id, str) and request_id:
        return request_id

    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None

    rid = headers.get("x-request-id") or headers.get("x-request_id")
    if isinstance(rid, str) and rid:
        return rid
    return None


def _build_responses_create_kwargs(
    *,
    model: str | None,
    input_data: Any,
    tools: Optional[Iterable[Any]] = None,
    tool_choice: Any = None,
    instructions: str | None = None,
    stream: bool = False,
    reasoning_summary: Optional[Literal["auto", "concise", "detailed"]] = None,
    previous_response_id: str | None = None,
    max_output_tokens: int | None = None,
    text_format: dict[str, Any] | None = None,
    metadata: dict[str, str] | None = None,
) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {
        "model": model,
        "input": input_data,
        # Must stay enabled for previous_response_id chaining across turns.
        "store": True,
    }

    if tools is not None:
        kwargs["tools"] = tools
    if tool_choice is not None:
        kwargs["tool_choice"] = tool_choice
    if instructions is not None:
        kwargs["instructions"] = instructions
    if stream:
        kwargs["stream"] = True
        kwargs["service_tier"] = "default"
    if reasoning_summary:
        kwargs["reasoning"] = {"summary": reasoning_summary}
    if previous_response_id:
        kwargs["previous_response_id"] = previous_response_id
    if max_output_tokens is not None:
        kwargs["max_output_tokens"] = max_output_tokens
    if text_format is not None:
        kwargs["text"] = text_format
    if metadata:
        kwargs["metadata"] = metadata

    return kwargs


def _response_text(response: Any) -> str:
    output_text = (getattr(response, "output_text", None) or "").strip()
    if output_text:
        return output_text

    collected: list[str] = []
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) != "message":
            continue
        for part in getattr(item, "content", []) or []:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text.strip():
                collected.append(text.strip())
    return "\n".join(collected).strip()


def _parse_response_json_object(response: Any) -> dict[str, Any] | None:
    text = _response_text(response)
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _format_structured_summary(payload: dict[str, Any]) -> str:
    summary_text = str(payload.get("summary") or "").strip()
    if not summary_text:
        return ""

    lines: list[str] = [summary_text]

    def _append_list_section(label: str, key: str) -> None:
        raw_items = payload.get(key)
        if not isinstance(raw_items, list):
            return
        items = [str(item).strip() for item in raw_items if str(item).strip()]
        if not items:
            return
        lines.append(f"{label}:")
        lines.extend(f"- {item}" for item in items)

    _append_list_section("Open tasks", "open_tasks")
    _append_list_section("Constraints", "constraints")
    _append_list_section("Decisions", "decisions")

    return "\n".join(lines).strip()


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

    if et in ("response.file_search_call.in_progress", "response.file_search_call.searching"):
        out.append(
            _build_status_event(
                stage="file_search.in_progress",
                phase="tool.file_search.searching",
                label="Searching files",
                source_event=et,
                event=event,
            )
        )
        return out

    if et == "response.file_search_call.completed":
        out.append(
            _build_status_event(
                stage="file_search.completed",
                phase="tool.file_search.completed",
                label="File search complete",
                source_event=et,
                event=event,
            )
        )
        out.append({"type": "file_search.used"})
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

    if et == "response.output_item.done" and getattr(event, "item", None) and event.item.type == "file_search_call":
        out.append(
            _build_status_event(
                stage="file_search.completed",
                phase="tool.file_search.completed",
                label="File search complete",
                source_event=et,
                event=event,
            )
        )
        out.append({"type": "file_search.used"})
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
        response_id = getattr(getattr(event, "response", None), "id", None)
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
        if response_id:
            out.append({"type": "response.meta", "response_id": response_id})
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
    model: Optional[str] = "gpt-5.4-nano",
    *,
    instructions: Optional[str] = "You are a helpful assistant.",
    tool_choice: Literal["none", "auto", "required"] | ToolChoiceAllowedParam | ToolChoiceTypesParam = "auto",
    tools: Optional[Iterable[FileSearchToolParam | WebSearchToolParam | CodeInterpreter | ImageGeneration]] = None,
    user_id: Optional[uuid.UUID] = None,
    conversation_id: Optional[uuid.UUID] = None,
    request_id: Optional[str] = None,
    reasoning_summary: Optional[Literal["auto", "concise", "detailed"]] = "concise",
    previous_response_id: Optional[str] = None,
    fallback_messages: Optional[List["Message"]] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    if tools is None:
        tools = default_tools

    corr_id = request_id or str(uuid.uuid4())
    request_metadata = {
        "request_id": corr_id,
        "conversation_id": str(conversation_id) if conversation_id else "",
        "user_id": str(user_id) if user_id else "",
    }
    usage = UsageTracker()
    state = StreamState()
    active_previous_response_id = previous_response_id
    active_messages = messages
    chain_attempted = bool(previous_response_id)
    chain_succeeded = False
    chain_fallback_reason: str | None = None

    try:
        response = None
        max_openai_retries = 3

        # Retry response creation
        for attempt in range(max_openai_retries):
            try:
                create_kwargs = _build_responses_create_kwargs(
                    model=model,
                    input_data=active_messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    instructions=(instructions or "") + STYLE_GUIDE,
                    stream=True,
                    reasoning_summary=reasoning_summary,
                    metadata=request_metadata,
                    previous_response_id=active_previous_response_id,
                )
                response = await client.responses.create(**create_kwargs)
                if active_previous_response_id:
                    chain_succeeded = True
                break
            except Exception as exc:
                # If server rejects previous_response_id, immediately fall back to non-chained create.
                err_text = str(exc).lower()
                if active_previous_response_id and "previous_response_id" in err_text:
                    logger.warning(
                        "OpenAI chaining fallback. request_id=%s previous_response_id=%s error=%s",
                        corr_id,
                        active_previous_response_id,
                        exc,
                    )
                    active_previous_response_id = None
                    if fallback_messages:
                        active_messages = fallback_messages
                    chain_fallback_reason = "create_rejected_previous_response_id"
                    continue
                retryable = _is_retryable_openai_exception(exc)
                upstream_request_id = _extract_openai_request_id(exc)
                is_last_attempt = attempt >= max_openai_retries - 1
                if retryable and not is_last_attempt:
                    delay = await _retry_delay_s(attempt)
                    logger.warning(
                        "OpenAI create failed (attempt %s/%s), retrying in %.2fs. request_id=%s upstream_request_id=%s error=%s",
                        attempt + 1,
                        max_openai_retries,
                        delay,
                        corr_id,
                        upstream_request_id,
                        exc,
                    )
                    await asyncio.sleep(delay)
                    continue

                logger.exception(
                    "OpenAI create failed permanently. request_id=%s upstream_request_id=%s",
                    corr_id,
                    upstream_request_id,
                )
                yield {
                    "type": "error",
                    "code": OPENAI_UPSTREAM_ERROR_CODE,
                    "data": OPENAI_UPSTREAM_USER_MESSAGE,
                }
                raise

        # Retry stream iteration
        for attempt in range(max_openai_retries):
            try:
                async for event in response:
                    mapped_events = await _map_openai_event(event=event, state=state, usage=usage)
                    for mapped in mapped_events:
                        yield mapped
                break
            except Exception as exc:
                retryable = _is_retryable_openai_exception(exc)
                upstream_request_id = _extract_openai_request_id(exc)
                is_last_attempt = attempt >= max_openai_retries - 1
                if retryable and not is_last_attempt:
                    delay = await _retry_delay_s(attempt)
                    logger.warning(
                        "OpenAI stream failed (attempt %s/%s), recreating stream in %.2fs. request_id=%s upstream_request_id=%s error=%s",
                        attempt + 1,
                        max_openai_retries,
                        delay,
                        corr_id,
                        upstream_request_id,
                        exc,
                    )
                    await asyncio.sleep(delay)

                    create_kwargs = _build_responses_create_kwargs(
                        model=model,
                        input_data=active_messages,
                        tools=tools,
                        tool_choice=tool_choice,
                        instructions=(instructions or "") + STYLE_GUIDE,
                        stream=True,
                        reasoning_summary=reasoning_summary,
                        metadata=request_metadata,
                        previous_response_id=active_previous_response_id,
                    )
                    response = await client.responses.create(**create_kwargs)
                    continue

                logger.exception(
                    "OpenAI stream failed permanently. request_id=%s upstream_request_id=%s",
                    corr_id,
                    upstream_request_id,
                )
                yield {
                    "type": "error",
                    "code": OPENAI_UPSTREAM_ERROR_CODE,
                    "data": OPENAI_UPSTREAM_USER_MESSAGE,
                }
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
        if chain_attempted and user_id:
            if chain_succeeded:
                track_event("openai.chain.succeeded", str(user_id), {"model": model})
            else:
                track_event(
                    "openai.chain.fallback",
                    str(user_id),
                    {"model": model, "reason": chain_fallback_reason or "create_rejected_previous_response_id"},
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
    except Exception as exc:
        if chain_attempted and user_id:
            track_event(
                "openai.chain.fallback",
                str(user_id),
                {"model": model, "reason": "exception_retry_exhausted"},
            )
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await log_usage(
                session,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=corr_id,
                provider="openai",
                model_name=model,
                status="error",
                error_message=str(exc),
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                reasoning_tokens=usage.reasoning_tokens,
                web_search_calls=usage.web_search_calls,
                images_generated=usage.images_generated,
            )
        raise


async def generate_conversation_title(first_message: str) -> str:
    try:
        response = await client.responses.create(
            **_build_responses_create_kwargs(
                model="gpt-5.4-nano",
                input_data=[
                    {
                        "role": "developer",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    "Return JSON only. Create a concise title for the user's message in five words or less. "
                                    "No punctuation and no quotation marks."
                                ),
                            }
                        ],
                    },
                    {"role": "user", "content": [{"type": "input_text", "text": first_message}]},
                ],
                max_output_tokens=30,
                text_format={
                    "format": {
                        "type": "json_schema",
                        "name": "conversation_title",
                        "schema": TITLE_JSON_SCHEMA,
                        "strict": True,
                    }
                },
            )
        )
        structured = _parse_response_json_object(response) or {}
        title = str(structured.get("title") or "").strip().strip('"').strip(".")
        if title:
            track_internal_event("openai.structured.title.success", {"model": "gpt-5.4-nano"})
            return title
        raise ValueError("Structured title payload missing title")
    except Exception as structured_exc:
        logger.warning("Structured title generation failed, falling back to text mode: %s", structured_exc)
        track_internal_event("openai.structured.title.fallback", {"model": "gpt-5.4-nano"})
    try:
        response = await client.responses.create(
            **_build_responses_create_kwargs(
                model="gpt-5.4-nano",
                input_data=[
                    {
                        "role": "developer",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    "You are an expert at creating short, concise titles. "
                                    "Summarize the user's message in 5 words or less. "
                                    "Do not use quotation marks or punctuation."
                                ),
                            }
                        ],
                    },
                    {"role": "user", "content": [{"type": "input_text", "text": first_message}]},
                ],
                max_output_tokens=20,
                text_format={"format": {"type": "text"}},
            )
        )
        logger.info("Generated conversation title")
        title = _response_text(response).strip().strip('"').strip(".")
        return title if title else "New Chat"
    except Exception as e:
        logger.warning("Title generation failed: %s", e)
        return "New Chat"


async def summarize_history_chunk(
    *,
    previous_summary: str | None,
    history_chunk: list[dict[str, Any]],
    model: str = "gpt-5.4-nano",
    max_output_tokens: int = 2000,
) -> str:
    if not history_chunk:
        return (previous_summary or "").strip()

    payload = {
        "previous_summary": previous_summary or "",
        "new_messages": history_chunk,
    }

    try:
        response = await client.responses.create(
            **_build_responses_create_kwargs(
                model=model,
                input_data=[
                    {
                        "role": "developer",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    SUMMARY_PROMPT
                                    + " Return JSON only following the provided schema."
                                ),
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    "Update the conversation summary using this JSON payload. "
                                    "Keep important details and remove redundancy.\n\n"
                                    f"{json.dumps(payload, ensure_ascii=False)}"
                                ),
                            }
                        ],
                    },
                ],
                max_output_tokens=max_output_tokens,
                text_format={
                    "format": {
                        "type": "json_schema",
                        "name": "history_summary",
                        "schema": SUMMARY_JSON_SCHEMA,
                        "strict": True,
                    }
                },
            )
        )
        structured = _parse_response_json_object(response) or {}
        formatted_summary = _format_structured_summary(structured)
        if formatted_summary:
            track_internal_event("openai.structured.summary.success", {"model": model})
            return formatted_summary
        raise ValueError("Structured summary payload missing summary text")
    except Exception as structured_exc:
        logger.warning("Structured summary failed, falling back to text mode: %s", structured_exc)
        track_internal_event("openai.structured.summary.fallback", {"model": model})
    try:
        response = await client.responses.create(
            **_build_responses_create_kwargs(
                model=model,
                input_data=[
                    {
                        "role": "developer",
                        "content": [{"type": "input_text", "text": SUMMARY_PROMPT}],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    "Update the conversation summary using this JSON payload. "
                                    "Keep important details and remove redundancy.\n\n"
                                    f"{json.dumps(payload, ensure_ascii=False)}"
                                ),
                            }
                        ],
                    },
                ],
                max_output_tokens=max_output_tokens,
                text_format={"format": {"type": "text"}},
            )
        )
    except Exception as exc:
        logger.warning("Failed to summarize conversation history: %s", exc)
        return (previous_summary or "").strip()

    output_text = _response_text(response)
    if output_text:
        return output_text

    return (previous_summary or "").strip()
