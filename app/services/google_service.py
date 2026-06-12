import base64
import importlib.util
import uuid
import logging
from typing import Any, AsyncGenerator, Iterable, Optional
from urllib.parse import unquote, urlparse

import httpx
from google import genai
from google.genai import types
from openai.types.responses import FileSearchToolParam, WebSearchToolParam
from openai.types.responses.tool import CodeInterpreter, ImageGeneration

from app.core.config import settings
from app.services.background.save_openai_usage import log_usage
from app.services.model_registry import GOOGLE_THINKING_MODELS, IMAGE_MODEL_PROVIDER, canonicalize_image_model
from app.db.database import engine
from sqlmodel.ext.asyncio.session import AsyncSession
from app.db.models import Message

logger = logging.getLogger(__name__)

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
GOOGLE_PROXY_ERROR_CODE = "GEMINI_PROXY_MISCONFIGURED"
GOOGLE_PROXY_USER_MESSAGE = "Gemini proxy is configured incorrectly on the server."
_SOCKS_PROXY_SCHEMES = {"socks4", "socks4a", "socks5", "socks5h"}
_REMOTE_DNS_SOCKS_PROXY_SCHEMES = {"socks4a", "socks5h"}


def _extract_tool_type(tool: Any) -> str | None:
    tool_type = getattr(tool, "type", None)
    return tool_type if isinstance(tool_type, str) and tool_type else None


def _extract_image_tool(
    tools: Optional[Iterable[FileSearchToolParam | WebSearchToolParam | CodeInterpreter | ImageGeneration]],
) -> ImageGeneration | dict[str, Any] | None:
    if not tools:
        return None
    for tool in tools:
        if isinstance(tool, dict) and str(tool.get("type") or "").strip().lower() == "image_generation":
            return tool
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


def _tool_choice_requests_image_generation(tool_choice: Any) -> bool:
    if isinstance(tool_choice, str):
        return tool_choice == "image_generation"
    if isinstance(tool_choice, list):
        requested = {
            str(item).strip().lower()
            for item in tool_choice
            if isinstance(item, str) and str(item).strip()
        }
        return "image_generation" in requested
    if isinstance(tool_choice, dict):
        tools_list = tool_choice.get("tools")
        if isinstance(tools_list, list):
            requested = {
                str(tool.get("type") or "").strip().lower()
                for tool in tools_list
                if isinstance(tool, dict)
            }
            return "image_generation" in requested
    return False


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


async def _interactions_steps_from_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    steps = []
    for message in history:
        role = str(message.get("role") or "user").lower()
        step_type = "model_output" if role == "assistant" else "user_input"
        content_parts = []
        for part in message.get("content", []):
            part_type = part.get("type")
            if part_type in {"input_text", "output_text"}:
                text = str(part.get("text") or "").strip()
                if text:
                    content_parts.append({"type": "text", "text": text})
            elif part_type == "input_image":
                image_url = str(part.get("image_url") or "").strip()
                if image_url:
                    inline_part = await _inline_image_part_from_url(image_url)
                    if inline_part:
                        content_parts.append({
                            "type": "image",
                            "data": inline_part["inlineData"]["data"],
                            "mime_type": inline_part["inlineData"]["mimeType"]
                        })
        if content_parts:
            steps.append({"type": step_type, "content": content_parts})
    return steps


async def _single_turn_input_from_history(history: list[dict[str, Any]]) -> Any:
    if not history:
        return ""
    msg = history[-1]
    parts = []
    for part in msg.get("content", []):
        part_type = part.get("type")
        if part_type in {"input_text", "output_text"}:
            text = str(part.get("text") or "").strip()
            if text:
                parts.append({"type": "text", "text": text})
        elif part_type == "input_image":
            image_url = str(part.get("image_url") or "").strip()
            if image_url:
                inline_part = await _inline_image_part_from_url(image_url)
                if inline_part:
                    parts.append({
                        "type": "image",
                        "data": inline_part["inlineData"]["data"],
                        "mime_type": inline_part["inlineData"]["mimeType"]
                    })
    if len(parts) == 1 and parts[0]["type"] == "text":
        return parts[0]["text"]
    return parts


def _generation_config_for_request(
    *,
    model: str,
    thinking_enabled: bool | None,
    reasoning_effort: str | None,
    image_size: str | None = None,
) -> dict[str, Any]:
    config = {}
    is_google_image_model = model in IMAGE_MODEL_PROVIDER
    if (
        not is_google_image_model
        and model not in GOOGLE_THINKING_MODELS
        and not reasoning_effort
        and not thinking_enabled
    ):
        return config

    thinking_level = (reasoning_effort or "").strip().lower() or None
    if thinking_level not in {"minimal", "low", "medium", "high"}:
        if thinking_enabled is False:
            thinking_level = "low"
        elif thinking_enabled:
            thinking_level = "low"
        else:
            thinking_level = None

    if thinking_level:
        # Gemini currently rejects the legacy "minimal" value for the affected
        # models and accepts only "low" and "high". Keep internal/legacy values
        # from being forwarded upstream.
        thinking_level = {
            "minimal": "low",
            "low": "low",
            "medium": "low",
            "high": "high",
        }.get(thinking_level)

    if thinking_level:
        config["thinking_level"] = thinking_level
        config["thinking_summaries"] = "auto"

    normalized_size = (image_size or "").strip().lower()
    if normalized_size:
        size_map = {"512": "512", "1k": "1K", "2k": "2K"}
        mapped_size = size_map.get(normalized_size)
        if mapped_size:
            config["image_config"] = {"image_size": mapped_size}

    return config


def _module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _build_google_socks_connector_kwargs(proxy_url: str) -> dict[str, Any]:
    parsed = urlparse(proxy_url)
    scheme = parsed.scheme.lower()
    if scheme not in _SOCKS_PROXY_SCHEMES:
        raise ValueError(f"Unsupported SOCKS proxy scheme: {scheme}")
    if not parsed.hostname or parsed.port is None:
        raise ValueError("SOCKS proxy URL must include host and port")

    proxy_type_name = "SOCKS5" if scheme.startswith("socks5") else "SOCKS4"
    return {
        "proxy_type_name": proxy_type_name,
        "host": parsed.hostname,
        "port": parsed.port,
        "username": unquote(parsed.username) if parsed.username else None,
        "password": unquote(parsed.password) if parsed.password else None,
        "rdns": scheme in _REMOTE_DNS_SOCKS_PROXY_SCHEMES,
    }


def _build_google_aiohttp_client(proxy_url: str) -> Any:
    import aiohttp

    proxy_scheme = urlparse(proxy_url).scheme.lower()
    session_kwargs: dict[str, Any] = {
        "trust_env": True,
    }

    if proxy_scheme in _SOCKS_PROXY_SCHEMES:
        if not _module_available("aiohttp_socks"):
            raise RuntimeError(
                "SOCKS proxy support for Gemini aiohttp transport requires the "
                "`aiohttp-socks` package."
            )
        from aiohttp_socks import ProxyConnector
        from python_socks import ProxyType

        connector_kwargs = _build_google_socks_connector_kwargs(proxy_url)
        proxy_type_name = connector_kwargs.pop("proxy_type_name")
        session_kwargs["connector"] = ProxyConnector(
            proxy_type=getattr(ProxyType, proxy_type_name),
            **connector_kwargs,
        )
    else:
        session_kwargs["proxy"] = proxy_url

    return aiohttp.ClientSession(**session_kwargs)


def _build_google_http_options() -> types.HttpOptions | None:
    proxy_url = (settings.GEMINI_PROXY_URL or "").strip()
    if not proxy_url:
        return None

    proxy_scheme = urlparse(proxy_url).scheme.lower()
    if proxy_scheme in _SOCKS_PROXY_SCHEMES and not _module_available("socksio"):
        raise RuntimeError(
            "SOCKS proxy support for Gemini requires the `socksio` package. "
            "Install `httpx[socks]` or add `socksio` directly."
        )

    client_args = {
        "proxy": proxy_url,
        "trust_env": True,
    }
    return types.HttpOptions(
        client_args=client_args.copy(),
        async_client_args=client_args.copy(),
        aiohttp_client=_build_google_aiohttp_client(proxy_url),
    )


def _build_google_client() -> genai.Client:
    http_options = _build_google_http_options()
    if http_options is None:
        return genai.Client(api_key=settings.GEMINI_API_KEY)
    client = genai.Client(api_key=settings.GEMINI_API_KEY, http_options=http_options)
    custom_aiohttp_client = getattr(http_options, "aiohttp_client", None)
    if custom_aiohttp_client is not None:
        setattr(client, "_codex_custom_aiohttp_client", custom_aiohttp_client)
    return client


async def _close_google_client(client: genai.Client) -> None:
    try:
        aio_client = getattr(client, "aio", None)
        aclose = getattr(aio_client, "aclose", None)
        if callable(aclose):
            await aclose()
    finally:
        custom_aiohttp_client = getattr(client, "_codex_custom_aiohttp_client", None)
        if custom_aiohttp_client is not None and not getattr(custom_aiohttp_client, "closed", False):
            await custom_aiohttp_client.close()


async def _log_google_error_usage(
    *,
    user_id: Optional[uuid.UUID],
    conversation_id: Optional[uuid.UUID],
    request_id: str,
    model_name: str,
    error_message: str,
) -> None:
    async with AsyncSession(engine, expire_on_commit=False) as db_session:
        await log_usage(
            db_session,
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=request_id,
            provider="google",
            model_name=model_name,
            status="error",
            error_message=error_message,
            input_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
            web_search_calls=0,
            images_generated=0,
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
    assistant_message_id: Optional[uuid.UUID],
    previous_interaction_id: Optional[str] = None,
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
    image_size = None
    # Keep ordinary Gemini chats on the selected text model. Only switch to the
    # image model when the request explicitly asks for image generation.
    image_enabled = bool(image_tool and _tool_choice_requests_image_generation(tool_choice))
    if image_enabled:
        if isinstance(image_tool, dict):
            request_model = canonicalize_image_model(image_tool.get("model") or model)
            image_size = image_tool.get("image_size")
        else:
            request_model = canonicalize_image_model(getattr(image_tool, "model", None) or model)

    yield {
        "type": "status",
        "stage": "queued",
        "phase": "response.created",
        "status": "active",
        "label": "Queued",
        "source_event": "google.interactions",
    }

    try:
        client = _build_google_client()
    except Exception as exc:
        logger.exception("Google client initialization failed request_id=%s model=%s", corr_id, request_model)
        yield {
            "type": "error",
            "code": GOOGLE_PROXY_ERROR_CODE,
            "data": GOOGLE_PROXY_USER_MESSAGE,
        }
        await _log_google_error_usage(
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=corr_id,
            model_name=request_model,
            error_message=str(exc),
        )
        return

    if previous_interaction_id:
        input_val = await _single_turn_input_from_history(messages)
    else:
        input_val = await _interactions_steps_from_history(messages)

    generation_config = _generation_config_for_request(
        model=request_model,
        thinking_enabled=thinking_enabled,
        reasoning_effort=reasoning_effort,
        image_size=image_size,
    )

    tools_payload = []
    if enabled_web_search:
        tools_payload.append({"type": "google_search"})

    system_text = ((instructions or "").strip() + "\n\n" + STYLE_GUIDE).strip()

    kwargs = {
        "model": request_model,
        "input": input_val,
        "stream": True,
        "system_instruction": system_text,
    }
    if image_enabled:
        # Keep both modalities in auto/required modes so Gemini can decide
        # whether to answer with text, image, or a mix.
        # Interactions API expects lowercase modality values.
        kwargs["response_modalities"] = ["text", "image"]
    if tools_payload:
        kwargs["tools"] = tools_payload
    if generation_config:
        kwargs["generation_config"] = generation_config
    if previous_interaction_id:
        kwargs["previous_interaction_id"] = previous_interaction_id

    try:
        stream = await client.aio.interactions.create(**kwargs)

        response_meta_sent = False
        thinking_started = False
        text_part_started = {}
        image_part_started = {}
        image_buffers = {}
        accumulated_thoughts = ""
        step_types = {}
        citations = []

        async for event in stream:
            et = event.event_type

            if et == "interaction.created":
                if event.interaction and event.interaction.id:
                    interaction_id = event.interaction.id
                    if not response_meta_sent:
                        yield {"type": "response.meta", "provider": "google", "interaction_id": interaction_id}
                        response_meta_sent = True

            elif et == "interaction.status_update":
                pass

            elif et == "step.start":
                if event.step:
                    step_types[event.index] = event.step.type
                    if event.step.type == "thought":
                        yield {
                            "type": "status",
                            "stage": "thinking",
                            "phase": "thinking",
                            "status": "active",
                            "label": "Thinking",
                            "source_event": "google.thinking",
                        }
                        thinking_started = True
                    elif event.step.type == "google_search_call":
                        yield {
                            "type": "status",
                            "stage": "web_search.in_progress",
                            "phase": "tool.web_search.searching",
                            "status": "active",
                            "label": "Searching the web",
                            "source_event": "google.google_search",
                        }

            elif et == "step.delta":
                delta = event.delta
                if not delta:
                    continue

                if delta.type == "text":
                    if event.index not in text_part_started:
                        yield {"type": "part.start", "index": event.index, "content_type": "text"}
                        text_part_started[event.index] = True
                    yield {"type": "text.delta", "index": event.index, "text": delta.text}

                elif delta.type == "thought_summary":
                    if not thinking_started:
                        yield {
                            "type": "status",
                            "stage": "thinking",
                            "phase": "thinking",
                            "status": "active",
                            "label": "Thinking",
                            "source_event": "google.thinking",
                        }
                        thinking_started = True

                    thought_text = ""
                    if delta.content and hasattr(delta.content, "text"):
                        thought_text = delta.content.text or ""
                    elif isinstance(delta.content, str):
                        thought_text = delta.content

                    accumulated_thoughts += thought_text
                    yield {
                        "type": "reasoning.summary.delta",
                        "delta": thought_text,
                        "output_index": 0,
                        "summary_index": 0,
                        "item_id": "google-thought-0",
                    }

                elif delta.type == "image":
                    if event.index not in image_part_started:
                        yield {"type": "part.start", "index": event.index, "content_type": "image"}
                        image_part_started[event.index] = True
                    if delta.data:
                        image_buffers.setdefault(event.index, "")
                        image_buffers[event.index] += delta.data

                elif delta.type == "text_annotation_delta":
                    if delta.annotations:
                        for ann in delta.annotations:
                            url = getattr(ann, "url", None)
                            title = getattr(ann, "title", None) or getattr(ann, "uri", url) or "Source"
                            if url:
                                if not any(c["url"] == url for c in citations):
                                    citations.append({"title": title, "url": url})

            elif et == "step.stop":
                st = step_types.get(event.index)

                if st == "thought":
                    yield {
                        "type": "reasoning.summary.done",
                        "text": accumulated_thoughts,
                        "output_index": 0,
                        "summary_index": 0,
                        "item_id": "google-thought-0",
                    }
                    yield {
                        "type": "status",
                        "stage": "thinking",
                        "phase": "thinking",
                        "status": "done",
                        "label": "Thinking complete",
                        "source_event": "google.thinking",
                    }

                elif st == "model_output":
                    if citations:
                        sources_text = "\n\n**Sources:**\n" + "\n".join(
                            f"[{i+1}] [{c['title']}]({c['url']})"
                            for i, c in enumerate(citations)
                        )
                        yield {"type": "text.delta", "index": event.index, "text": sources_text}
                    if event.index in text_part_started:
                        yield {"type": "text.done", "index": event.index}

                    if event.index in image_part_started:
                        image_b64 = image_buffers.get(event.index, "")
                        yield {
                            "type": "image.ready",
                            "index": event.index,
                            "format": "b64",
                            "data": image_b64,
                        }
                        yield {
                            "type": "status",
                            "stage": "image_generation.completed",
                            "phase": "tool.image_generation.completed",
                            "status": "done",
                            "label": "Image generated",
                            "source_event": "google.interactions",
                            "index": event.index,
                        }

                elif st == "google_search_call":
                    pass
                elif st == "google_search_result":
                    yield {
                        "type": "status",
                        "stage": "web_search.completed",
                        "phase": "tool.web_search.completed",
                        "status": "done",
                        "label": "Web search complete",
                        "source_event": "google.google_search",
                    }

            elif et == "interaction.completed":
                usage_meta = event.interaction.usage if event.interaction else None
                input_tokens = usage_meta.total_input_tokens if usage_meta else 0
                output_tokens = usage_meta.total_output_tokens if usage_meta else 0
                reasoning_tokens = usage_meta.total_thought_tokens if usage_meta else 0

                async with AsyncSession(engine, expire_on_commit=False) as db_session:
                    if accumulated_thoughts and assistant_message_id:
                        message = await db_session.get(Message, assistant_message_id)
                        if message:
                            message.reasoning_summary = accumulated_thoughts
                            db_session.add(message)
                            await db_session.commit()
                    await log_usage(
                        db_session,
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
                        images_generated=len(image_part_started),
                    )

                yield {
                    "type": "status",
                    "stage": "completed",
                    "phase": "response.completed",
                    "status": "done",
                    "label": "Completed",
                    "source_event": "google.interactions",
                }
                yield {"type": "done"}

            elif et == "error":
                err_msg = event.error.message if event.error else "Unknown upstream Google error"
                logger.error("Interactions stream ErrorEvent: %s", err_msg)
                yield {
                    "type": "error",
                    "code": GOOGLE_UPSTREAM_ERROR_CODE,
                    "data": GOOGLE_UPSTREAM_USER_MESSAGE,
                }
                await _log_google_error_usage(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    request_id=corr_id,
                    model_name=request_model,
                    error_message=err_msg,
                )
                return

    except Exception as exc:
        logger.exception("Google interactions stream failed request_id=%s model=%s", corr_id, request_model)
        yield {
            "type": "error",
            "code": GOOGLE_UPSTREAM_ERROR_CODE,
            "data": GOOGLE_UPSTREAM_USER_MESSAGE,
        }
        await _log_google_error_usage(
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=corr_id,
            model_name=request_model,
            error_message=str(exc),
        )
        return
    finally:
        await _close_google_client(client)
