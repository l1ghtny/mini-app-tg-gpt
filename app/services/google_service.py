import base64
import uuid
from typing import Any, AsyncGenerator, Iterable, Optional

import httpx
from openai.types.responses import FileSearchToolParam, WebSearchToolParam
from openai.types.responses.tool import CodeInterpreter, ImageGeneration

from app.core.config import settings
from app.services.background.save_openai_usage import log_usage
from app.services.model_registry import GOOGLE_THINKING_MODELS
from app.db.database import engine
from sqlmodel.ext.asyncio.session import AsyncSession

STYLE_GUIDE = (
    "Format replies in Markdown:\n"
    "- Use proper headings for sections (##, ###).\n"
    "- Use bullet lists with '-' and numbered lists with '1.' (not '1)')\n"
    "- Use fenced code blocks for code.\n"
    "- Use standard [text](url) links.\n"
    "Only use headings, bullet lists, and others when it is applicable, don't use big headings for short messages"
)

GOOGLE_UPSTREAM_ERROR_CODE = "GOOGLE_UPSTREAM_UNAVAILABLE"
GOOGLE_UPSTREAM_USER_MESSAGE = "Sorry, Google Gemini has some issues on their end. Please try again in a moment."


def _extract_tool_type(tool: Any) -> str | None:
    tool_type = getattr(tool, "type", None)
    return tool_type if isinstance(tool_type, str) and tool_type else None


def _extract_image_tool(
    tools: Optional[Iterable[FileSearchToolParam | WebSearchToolParam | CodeInterpreter | ImageGeneration]],
) -> ImageGeneration | None:
    if not tools:
        return None
    for tool in tools:
        if isinstance(tool, ImageGeneration):
            return tool
    return None


def _has_tool(
    tools: Optional[Iterable[FileSearchToolParam | WebSearchToolParam | CodeInterpreter | ImageGeneration]],
    tool_name: str,
) -> bool:
    if not tools:
        return False
    return any(_extract_tool_type(tool) == tool_name for tool in tools)


async def _inline_image_part_from_url(url: str) -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
            response = await client.get(url)
            response.raise_for_status()
    except Exception:
        return None

    content_type = (response.headers.get("content-type") or "image/png").split(";", 1)[0].strip()
    return {
        "inlineData": {
            "mimeType": content_type or "image/png",
            "data": base64.b64encode(response.content).decode("ascii"),
        }
    }


async def _google_contents_from_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contents: list[dict[str, Any]] = []

    for message in history:
        role = str(message.get("role") or "user").lower()
        google_role = "model" if role == "assistant" else "user"
        parts: list[dict[str, Any]] = []

        for part in message.get("content", []):
            part_type = part.get("type")
            if part_type in {"input_text", "output_text"}:
                text = str(part.get("text") or "").strip()
                if text:
                    parts.append({"text": text})
            elif part_type == "input_image":
                image_url = str(part.get("image_url") or "").strip()
                if image_url:
                    inline_part = await _inline_image_part_from_url(image_url)
                    if inline_part:
                        parts.append(inline_part)

        if parts:
            contents.append({"role": google_role, "parts": parts})

    return contents


def _thinking_config_for_request(
    *,
    model: str,
    thinking_enabled: bool | None,
    reasoning_effort: str | None,
) -> dict[str, Any] | None:
    if model not in GOOGLE_THINKING_MODELS and not reasoning_effort and not thinking_enabled:
        return None

    thinking_level = (reasoning_effort or "").strip().lower() or None
    if thinking_level not in {"minimal", "low", "medium", "high"}:
        if thinking_enabled is False:
            thinking_level = "minimal"
        elif thinking_enabled:
            thinking_level = "medium"
        else:
            thinking_level = None

    config: dict[str, Any] = {}
    if thinking_level:
        config["thinkingLevel"] = thinking_level
    if thinking_enabled or reasoning_effort:
        config["includeThoughts"] = True
    return config or None


def _candidate_parts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = payload.get("candidates") or []
    if not candidates:
        return []
    content = candidates[0].get("content") or {}
    return content.get("parts") or []


def _usage_tuple(payload: dict[str, Any]) -> tuple[int, int, int]:
    usage = payload.get("usageMetadata") or {}
    return (
        int(usage.get("promptTokenCount") or 0),
        int(usage.get("candidatesTokenCount") or 0),
        int(usage.get("thoughtsTokenCount") or 0),
    )


async def stream_normalized_google_response(
    messages: list[dict[str, Any]],
    model: str,
    *,
    instructions: Optional[str],
    tool_choice: Any,
    tools: Optional[Iterable[FileSearchToolParam | WebSearchToolParam | CodeInterpreter | ImageGeneration]],
    user_id: Optional[uuid.UUID],
    conversation_id: Optional[uuid.UUID],
    request_id: Optional[str],
    thinking_enabled: bool | None = None,
    reasoning_effort: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    corr_id = request_id or str(uuid.uuid4())
    if not settings.GEMINI_API_KEY:
        yield {
            "type": "error",
            "code": "GEMINI_API_KEY_MISSING",
            "data": "Gemini is not configured on the server.",
        }
        return

    enabled_web_search = _has_tool(tools, "web_search")
    image_tool = _extract_image_tool(tools)
    request_model = model
    if image_tool and _tool_choice_explicitly_requests_image_generation(tool_choice):
        request_model = getattr(image_tool, "model", None) or model

    yield {
        "type": "status",
        "stage": "queued",
        "phase": "response.created",
        "status": "active",
        "label": "Queued",
        "source_event": "google.generateContent",
    }

    thinking_config = _thinking_config_for_request(
        model=model,
        thinking_enabled=thinking_enabled,
        reasoning_effort=reasoning_effort,
    )
    if thinking_config and thinking_config.get("includeThoughts"):
        yield {
            "type": "status",
            "stage": "thinking",
            "phase": "thinking",
            "status": "active",
            "label": "Thinking",
            "source_event": "google.thinking",
        }

    if enabled_web_search:
        yield {
            "type": "status",
            "stage": "web_search.in_progress",
            "phase": "tool.web_search.searching",
            "status": "active",
            "label": "Searching the web",
            "source_event": "google.google_search",
        }

    try:
        contents = await _google_contents_from_history(messages)
        body: dict[str, Any] = {"contents": contents}
        system_text = ((instructions or "").strip() + "\n\n" + STYLE_GUIDE).strip()
        if system_text:
            body["systemInstruction"] = {"parts": [{"text": system_text}]}

        tools_payload: list[dict[str, Any]] = []
        if enabled_web_search and not image_tool:
            tools_payload.append({"googleSearch": {}})
        if tools_payload:
            body["tools"] = tools_payload

        generation_config: dict[str, Any] = {}
        if thinking_config:
            generation_config["thinkingConfig"] = thinking_config
        if generation_config:
            body["generationConfig"] = generation_config

        url = f"{settings.GEMINI_API_BASE_URL}/models/{request_model}:generateContent"
        headers = {
            "x-goog-api-key": settings.GEMINI_API_KEY,
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=10.0)) as client:
            response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            payload = response.json()

        input_tokens, output_tokens, reasoning_tokens = _usage_tuple(payload)

        if enabled_web_search and (payload.get("candidates") or [{}])[0].get("groundingMetadata"):
            yield {
                "type": "status",
                "stage": "web_search.completed",
                "phase": "tool.web_search.completed",
                "status": "done",
                "label": "Web search complete",
                "source_event": "google.google_search",
            }

        answer_text_parts: list[str] = []
        image_index = 0
        thought_index = 0
        for part in _candidate_parts(payload):
            text = part.get("text")
            if isinstance(text, str) and text:
                if part.get("thought") is True:
                    yield {
                        "type": "reasoning.summary.delta",
                        "delta": text,
                        "output_index": 0,
                        "summary_index": thought_index,
                        "item_id": f"google-thought-{thought_index}",
                    }
                    yield {
                        "type": "reasoning.summary.done",
                        "text": text,
                        "output_index": 0,
                        "summary_index": thought_index,
                        "item_id": f"google-thought-{thought_index}",
                    }
                    thought_index += 1
                else:
                    answer_text_parts.append(text)

            inline_data = part.get("inlineData") or {}
            image_b64 = inline_data.get("data")
            mime_type = inline_data.get("mimeType")
            if image_b64 and isinstance(image_b64, str) and str(mime_type).startswith("image/"):
                yield {"type": "part.start", "index": image_index, "content_type": "image"}
                yield {
                    "type": "image.ready",
                    "index": image_index,
                    "format": "b64",
                    "data": image_b64,
                }
                yield {
                    "type": "status",
                    "stage": "image_generation.completed",
                    "phase": "tool.image_generation.completed",
                    "status": "done",
                    "label": "Image generated",
                    "source_event": "google.generateContent",
                    "index": image_index,
                }
                image_index += 1

        if thinking_config and thinking_config.get("includeThoughts"):
            yield {
                "type": "status",
                "stage": "thinking",
                "phase": "thinking",
                "status": "done",
                "label": "Thinking complete",
                "source_event": "google.thinking",
            }

        answer_text = "".join(answer_text_parts).strip()
        if answer_text:
            yield {"type": "part.start", "index": 0, "content_type": "text"}
            yield {"type": "text.delta", "index": 0, "text": answer_text}
            yield {"type": "text.done", "index": 0}

        yield {
            "type": "status",
            "stage": "completed",
            "phase": "response.completed",
            "status": "done",
            "label": "Completed",
            "source_event": "google.generateContent",
        }
        yield {"type": "done"}

        async with AsyncSession(engine, expire_on_commit=False) as session:
            await log_usage(
                session,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=corr_id,
                provider="google",
                model_name=request_model,
                status="success",
                error_message=None,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                web_search_calls=1 if enabled_web_search else 0,
                images_generated=image_index,
            )

    except Exception as exc:
        yield {
            "type": "error",
            "code": GOOGLE_UPSTREAM_ERROR_CODE,
            "data": GOOGLE_UPSTREAM_USER_MESSAGE,
        }
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await log_usage(
                session,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=corr_id,
                provider="google",
                model_name=request_model,
                status="error",
                error_message=str(exc),
                input_tokens=0,
                output_tokens=0,
                reasoning_tokens=0,
                web_search_calls=0,
                images_generated=0,
            )


def _tool_choice_explicitly_requests_image_generation(tool_choice: Any) -> bool:
    if tool_choice == "image_generation":
        return True
    if isinstance(tool_choice, dict):
        tools = tool_choice.get("tools")
        if isinstance(tools, list):
            requested = {
                str(tool.get("type") or "").strip().lower()
                for tool in tools
                if isinstance(tool, dict)
            }
            return requested == {"image_generation"}
    return False
