import uuid
from decimal import Decimal
from typing import Any, AsyncGenerator, Optional

import httpx
from openai import APIError, AsyncOpenAI, AuthenticationError, NotFoundError
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.db.database import engine
from app.db.models import TokenUsage
from app.services.background.save_openai_usage import log_usage

PERPLEXITY_UPSTREAM_ERROR_CODE = "PERPLEXITY_UPSTREAM_UNAVAILABLE"
PERPLEXITY_UPSTREAM_USER_MESSAGE = "Sorry, Perplexity Search has some issues on their end. Please try again in a moment."
PERPLEXITY_AGENT_TOOLS = {"fetch_url", "finance_search"}
PERPLEXITY_AGENT_TOOL_ALIASES = {
    "read_url": "fetch_url",
    "url_fetch": "fetch_url",
    "finance": "finance_search",
}
PERPLEXITY_SEARCH_MODE_CONTEXT_SIZE = {
    "quick": "low",
    "standard": "medium",
    "deep": "high",
}

STYLE_GUIDE = (
    "Format replies in Markdown:\n"
    "- Use proper headings for sections (##, ###).\n"
    "- Use bullet lists with '-' and numbered lists with '1.' (not '1)')\n"
    "- Use fenced code blocks for code.\n"
    "- Use standard [text](url) links.\n"
    "Only use headings, bullet lists, and others when it is applicable, don't use big headings for short messages"
)


def _build_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=settings.PERPLEXITY_API_KEY,
        base_url=settings.PERPLEXITY_API_BASE_URL.rstrip("/"),
    )


def _agent_url() -> str:
    base = settings.PERPLEXITY_API_BASE_URL.rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/agent"
    return f"{base}/v1/agent"


def _resolve_search_context_size(search_mode: str | None) -> str:
    if search_mode:
        return PERPLEXITY_SEARCH_MODE_CONTEXT_SIZE.get(
            search_mode,
            settings.PERPLEXITY_SEARCH_CONTEXT_SIZE,
        )
    return settings.PERPLEXITY_SEARCH_CONTEXT_SIZE


def _normalize_agent_tool_name(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("type")
    elif not isinstance(value, str):
        value = getattr(value, "type", None)

    if not isinstance(value, str):
        return None
    raw = value.strip().lower()
    if not raw:
        return None
    return PERPLEXITY_AGENT_TOOL_ALIASES.get(raw, raw)


def _requested_tool_names(tool_choice: Any) -> set[str]:
    if isinstance(tool_choice, list):
        return {
            normalized
            for item in tool_choice
            for normalized in [_normalize_agent_tool_name(item)]
            if normalized
        }

    normalized = _normalize_agent_tool_name(tool_choice)
    return {normalized} if normalized else set()


def _requested_agent_tools(tool_choice: Any) -> list[dict[str, Any]]:
    requested = _requested_tool_names(tool_choice)
    tools: list[dict[str, Any]] = []
    if "web_search" in requested:
        tools.append({"type": "web_search"})
    if "fetch_url" in requested:
        tools.append({"type": "fetch_url", "max_urls": 3})
    if "finance_search" in requested:
        tools.append({"type": "finance_search"})
    return tools


def _flatten_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")

    chunks: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type in {"input_text", "output_text", "text"}:
            text = str(part.get("text") or "").strip()
            if text:
                chunks.append(text)
        elif part_type in {"input_image", "image_url", "image"}:
            chunks.append("[Image omitted: Perplexity Sonar is text-only in this app.]")
    return "\n\n".join(chunks).strip()


def _to_chat_messages(
    messages: list[dict[str, Any]],
    *,
    instructions: Optional[str],
) -> list[dict[str, str]]:
    chat_messages: list[dict[str, str]] = []
    system_text = ((instructions or "").strip() + "\n\n" + STYLE_GUIDE).strip()
    if system_text:
        chat_messages.append({"role": "system", "content": system_text})

    for message in messages:
        role = message.get("role")
        if role not in {"system", "user", "assistant"}:
            continue
        content = _flatten_content(message.get("content")).strip()
        if not content:
            continue
        chat_messages.append({"role": role, "content": content})

    if not any(message["role"] == "user" for message in chat_messages):
        chat_messages.append({"role": "user", "content": ""})
    return chat_messages


def _to_agent_input(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = _flatten_content(message.get("content")).strip()
        if content:
            lines.append(f"{role.upper()}:\n{content}")
    return "\n\n".join(lines).strip() or ""


def _agent_model_name(model: str) -> str:
    if "/" in model:
        return model
    return f"perplexity/{model}"


def _append_source(
    sources: list[tuple[str | None, str]],
    seen_urls: set[str],
    url: Any,
    title: Any = None,
) -> None:
    if not isinstance(url, str) or not url:
        return
    if url in seen_urls:
        return
    seen_urls.add(url)
    source_title = title.strip() if isinstance(title, str) and title.strip() else None
    sources.append((source_title, url))


def _extract_agent_text_and_sources(response: dict[str, Any]) -> tuple[str, list[tuple[str | None, str]]]:
    output_text = response.get("output_text")
    text_parts: list[str] = [output_text.strip()] if isinstance(output_text, str) and output_text.strip() else []
    sources: list[tuple[str | None, str]] = []
    seen_urls: set[str] = set()

    for item in response.get("output") or []:
        if not isinstance(item, dict):
            continue

        item_type = item.get("type")
        if item_type == "message" and not text_parts:
            for content in item.get("content") or []:
                if not isinstance(content, dict):
                    continue
                if content.get("type") in {"output_text", "text"}:
                    text = content.get("text")
                    if isinstance(text, str) and text.strip():
                        text_parts.append(text.strip())

        if item_type == "search_results":
            for result in item.get("results") or []:
                if isinstance(result, dict):
                    _append_source(sources, seen_urls, result.get("url"), result.get("title"))

        if item_type == "fetch_url_results":
            for result in (item.get("contents") or item.get("results") or []):
                if isinstance(result, dict):
                    _append_source(sources, seen_urls, result.get("url"), result.get("title"))

        if item_type == "finance_results":
            for result in item.get("results") or []:
                if not isinstance(result, dict):
                    continue
                for url in result.get("sources") or []:
                    tickers = result.get("tickers")
                    title = ", ".join(tickers) if isinstance(tickers, list) else result.get("category")
                    _append_source(sources, seen_urls, url, title)

    return "\n\n".join(text_parts).strip(), sources


def _format_sources(sources: list[tuple[str | None, str]]) -> str:
    if not sources:
        return ""
    lines = []
    for index, (title, url) in enumerate(sources, start=1):
        if title:
            lines.append(f"[{index}] {title} - {url}")
        else:
            lines.append(f"[{index}] {url}")
    return "\n\n**Sources:**\n" + "\n".join(lines)


def _extract_delta_text(chunk: Any, previous_text: str) -> tuple[str, str]:
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return "", previous_text

    delta = getattr(choices[0], "delta", None)
    content = getattr(delta, "content", None)
    if not isinstance(content, str) or not content:
        return "", previous_text

    # Some Perplexity-compatible streams historically sent the accumulated
    # content. Preserve normal deltas while guarding against duplicated text.
    if previous_text and content.startswith(previous_text):
        return content[len(previous_text):], content
    return content, previous_text + content


def _usage_value(usage: Any, *names: str) -> int:
    for name in names:
        value = getattr(usage, name, None)
        if isinstance(value, int):
            return value
    return 0


def _usage_dict_value(usage: dict[str, Any], *names: str) -> int:
    for name in names:
        value = usage.get(name)
        if isinstance(value, int):
            return value
    return 0


def _nested_dict(source: dict[str, Any], key: str) -> dict[str, Any]:
    value = source.get(key)
    return value if isinstance(value, dict) else {}


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _decimal_from_dict(source: dict[str, Any], *names: str) -> Decimal | None:
    for name in names:
        parsed = _decimal_or_none(source.get(name))
        if parsed is not None:
            return parsed
    return None


def _agent_tool_invocations(usage: dict[str, Any], requested_tool_count: int) -> int:
    details = _nested_dict(usage, "tool_calls_details")
    total = 0
    for tool_name in ("web_search", "fetch_url", "finance_search"):
        tool_details = _nested_dict(details, tool_name)
        invocation = tool_details.get("invocation")
        if isinstance(invocation, int):
            total += invocation
    return total or requested_tool_count


async def _log_perplexity_usage(
    *,
    user_id: Optional[uuid.UUID],
    conversation_id: Optional[uuid.UUID],
    request_id: str,
    model_name: str,
    status: str,
    error_message: str | None,
    input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int,
    web_search_calls: int,
    currency: str = "USD",
    cost_input: Decimal | None = None,
    cost_output: Decimal | None = None,
    cost_reasoning: Decimal | None = None,
    cost_web_search: Decimal | None = None,
    total_cost: Decimal | None = None,
) -> None:
    async with AsyncSession(engine, expire_on_commit=False) as db_session:
        if any(
            value is not None
            for value in (cost_input, cost_output, cost_reasoning, cost_web_search, total_cost)
        ):
            input_cost = cost_input or Decimal("0")
            output_cost = cost_output or Decimal("0")
            reasoning_cost = cost_reasoning or Decimal("0")
            tool_cost = cost_web_search or Decimal("0")
            total = total_cost or (input_cost + output_cost + reasoning_cost + tool_cost)
            usage_row = TokenUsage(
                user_id=user_id,
                conversation_id=conversation_id,
                provider="perplexity",
                model_name=model_name,
                request_id=request_id,
                status=status,
                error_message=error_message,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                web_search_calls=web_search_calls,
                images_generated=0,
                currency=currency,
                cost_input=input_cost,
                cost_output=output_cost,
                cost_reasoning=reasoning_cost,
                cost_web_search=tool_cost,
                cost_images=Decimal("0"),
                total_cost=total,
            )
            db_session.add(usage_row)
            await db_session.commit()
            return

        await log_usage(
            db_session,
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=request_id,
            provider="perplexity",
            model_name=model_name,
            status=status,
            error_message=error_message,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            web_search_calls=web_search_calls,
            images_generated=0,
        )


async def _post_agent_response(payload: dict[str, Any]) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {settings.PERPLEXITY_API_KEY}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=90.0) as client:
        response = await client.post(_agent_url(), headers=headers, json=payload)
        response.raise_for_status()
        return response.json()


async def _stream_normalized_perplexity_agent_response(
    messages: list[dict[str, Any]],
    model: str,
    *,
    instructions: Optional[str],
    tool_choice: Any,
    user_id: Optional[uuid.UUID],
    conversation_id: Optional[uuid.UUID],
    request_id: str,
) -> AsyncGenerator[dict[str, Any], None]:
    tools = _requested_agent_tools(tool_choice)
    tool_names = {tool["type"] for tool in tools}
    if not tools:
        return

    if "finance_search" in tool_names:
        tool_label = "Searching finance data"
        tool_stage = "finance_search"
    elif "fetch_url" in tool_names:
        tool_label = "Reading URL"
        tool_stage = "fetch_url"
    else:
        tool_label = "Searching the web"
        tool_stage = "web_search"

    yield {
        "type": "status",
        "stage": "queued",
        "phase": "response.created",
        "status": "active",
        "label": "Queued",
        "source_event": "perplexity.agent",
    }
    yield {
        "type": "status",
        "stage": f"{tool_stage}.in_progress",
        "phase": f"tool.{tool_stage}.running",
        "status": "active",
        "label": tool_label,
        "source_event": "perplexity.agent",
    }

    payload = {
        "model": _agent_model_name(model),
        "input": _to_agent_input(messages),
        "instructions": ((instructions or "").strip() + "\n\n" + STYLE_GUIDE).strip(),
        "tools": tools,
        "tool_choice": "auto",
        "max_steps": 4,
        "max_output_tokens": 4096,
    }

    input_tokens = 0
    output_tokens = 0
    reasoning_tokens = 0
    tool_invocations = len(tools)

    try:
        response = await _post_agent_response(payload)
        response_id = response.get("id")
        if isinstance(response_id, str) and response_id:
            yield {"type": "response.meta", "provider": "perplexity", "response_id": response_id}

        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        input_tokens = _usage_dict_value(usage, "input_tokens", "prompt_tokens")
        output_tokens = _usage_dict_value(usage, "output_tokens", "completion_tokens")
        output_details = _nested_dict(usage, "output_tokens_details")
        reasoning_tokens = _usage_dict_value(usage, "reasoning_tokens") or _usage_dict_value(
            output_details,
            "reasoning_tokens",
        )
        tool_invocations = _agent_tool_invocations(usage, len(tools))

        text, sources = _extract_agent_text_and_sources(response)
        final_text = text + _format_sources(sources)
        if final_text:
            yield {"type": "part.start", "index": 0, "content_type": "text"}
            yield {"type": "text.delta", "index": 0, "text": final_text}
            yield {"type": "text.done", "index": 0}

        yield {
            "type": "status",
            "stage": f"{tool_stage}.completed",
            "phase": f"tool.{tool_stage}.completed",
            "status": "done",
            "label": f"{tool_label} complete",
            "source_event": "perplexity.agent",
        }
        yield {
            "type": "status",
            "stage": "completed",
            "phase": "response.completed",
            "status": "done",
            "label": "Completed",
            "source_event": "perplexity.agent",
        }
        yield {"type": "done"}

        cost = _nested_dict(usage, "cost")
        await _log_perplexity_usage(
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=request_id,
            model_name=model,
            status="success",
            error_message=None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            web_search_calls=tool_invocations,
            currency=str(cost.get("currency") or "USD"),
            cost_input=_decimal_from_dict(cost, "input_cost", "input_tokens_cost"),
            cost_output=_decimal_from_dict(cost, "output_cost", "output_tokens_cost"),
            cost_reasoning=_decimal_from_dict(cost, "reasoning_cost", "reasoning_tokens_cost"),
            cost_web_search=_decimal_from_dict(cost, "tool_calls_cost", "search_queries_cost", "request_cost"),
            total_cost=_decimal_from_dict(cost, "total_cost"),
        )

    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        if status_code in {401, 403}:
            yield {"type": "error", "data": "Perplexity authentication failed. Check API key."}
            error_message = "authentication_error"
        elif status_code == 404:
            yield {"type": "error", "data": "Perplexity model or Agent endpoint not found."}
            error_message = "model_or_endpoint_not_found"
        else:
            yield {
                "type": "error",
                "code": PERPLEXITY_UPSTREAM_ERROR_CODE,
                "data": PERPLEXITY_UPSTREAM_USER_MESSAGE,
            }
            error_message = str(exc)
        await _log_perplexity_usage(
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=request_id,
            model_name=model,
            status="error",
            error_message=error_message,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            web_search_calls=0,
        )
        if status_code not in {401, 403, 404}:
            raise
    except httpx.HTTPError as exc:
        yield {
            "type": "error",
            "code": PERPLEXITY_UPSTREAM_ERROR_CODE,
            "data": PERPLEXITY_UPSTREAM_USER_MESSAGE,
        }
        await _log_perplexity_usage(
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=request_id,
            model_name=model,
            status="error",
            error_message=str(exc),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            web_search_calls=0,
        )
        raise
    except Exception as exc:
        await _log_perplexity_usage(
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=request_id,
            model_name=model,
            status="error",
            error_message=str(exc),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            web_search_calls=0,
        )
        raise


async def stream_normalized_perplexity_response(
    messages: list[dict[str, Any]],
    model: str,
    *,
    instructions: Optional[str],
    tool_choice: Any = "auto",
    search_mode: str | None = None,
    user_id: Optional[uuid.UUID],
    conversation_id: Optional[uuid.UUID],
    request_id: Optional[str],
) -> AsyncGenerator[dict[str, Any], None]:
    corr_id = request_id or str(uuid.uuid4())
    if not settings.PERPLEXITY_API_KEY:
        yield {
            "type": "error",
            "code": "PERPLEXITY_API_KEY_MISSING",
            "data": "Perplexity is not configured on the server.",
        }
        return

    if _requested_tool_names(tool_choice) & PERPLEXITY_AGENT_TOOLS:
        async for event in _stream_normalized_perplexity_agent_response(
            messages,
            model,
            instructions=instructions,
            tool_choice=tool_choice,
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=corr_id,
        ):
            yield event
        return

    yield {
        "type": "status",
        "stage": "queued",
        "phase": "response.created",
        "status": "active",
        "label": "Queued",
        "source_event": "perplexity.chat.completions",
    }
    yield {
        "type": "status",
        "stage": "web_search.in_progress",
        "phase": "tool.web_search.searching",
        "status": "active",
        "label": "Searching the web",
        "source_event": "perplexity.sonar",
    }

    input_tokens = 0
    output_tokens = 0
    reasoning_tokens = 0
    previous_text = ""
    text_started = False
    citations: list[str] = []

    try:
        client = _build_client()
        stream = await client.chat.completions.create(
            model=model,
            messages=_to_chat_messages(messages, instructions=instructions),
            stream=True,
            extra_body={
                "web_search_options": {
                    "search_context_size": _resolve_search_context_size(search_mode),
                }
            },
        )

        response_meta_sent = False
        async for chunk in stream:
            chunk_id = getattr(chunk, "id", None)
            if chunk_id and not response_meta_sent:
                yield {"type": "response.meta", "provider": "perplexity", "response_id": chunk_id}
                response_meta_sent = True

            for url in getattr(chunk, "citations", None) or []:
                if isinstance(url, str) and url and url not in citations:
                    citations.append(url)

            usage = getattr(chunk, "usage", None)
            if usage:
                input_tokens = _usage_value(usage, "prompt_tokens", "input_tokens") or input_tokens
                output_tokens = _usage_value(usage, "completion_tokens", "output_tokens") or output_tokens
                reasoning_tokens = _usage_value(usage, "reasoning_tokens") or reasoning_tokens

            delta_text, previous_text = _extract_delta_text(chunk, previous_text)
            if not delta_text:
                continue
            if not text_started:
                yield {"type": "part.start", "index": 0, "content_type": "text"}
                text_started = True
            yield {"type": "text.delta", "index": 0, "text": delta_text}

        if citations:
            if not text_started:
                yield {"type": "part.start", "index": 0, "content_type": "text"}
                text_started = True
            sources = "\n\n**Sources:**\n" + "\n".join(f"[{i + 1}] {url}" for i, url in enumerate(citations))
            yield {"type": "text.delta", "index": 0, "text": sources}

        if text_started:
            yield {"type": "text.done", "index": 0}

        yield {
            "type": "status",
            "stage": "web_search.completed",
            "phase": "tool.web_search.completed",
            "status": "done",
            "label": "Web search complete",
            "source_event": "perplexity.sonar",
        }
        yield {
            "type": "status",
            "stage": "completed",
            "phase": "response.completed",
            "status": "done",
            "label": "Completed",
            "source_event": "perplexity.chat.completions",
        }
        yield {"type": "done"}

        await _log_perplexity_usage(
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=corr_id,
            model_name=model,
            status="success",
            error_message=None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            web_search_calls=1,
        )

    except AuthenticationError:
        yield {"type": "error", "data": "Perplexity authentication failed. Check API key."}
        await _log_perplexity_usage(
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=corr_id,
            model_name=model,
            status="error",
            error_message="authentication_error",
            input_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
            web_search_calls=0,
        )
    except NotFoundError:
        yield {"type": "error", "data": "Perplexity model not found. Please check the model name."}
        await _log_perplexity_usage(
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=corr_id,
            model_name=model,
            status="error",
            error_message="model_not_found",
            input_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
            web_search_calls=0,
        )
    except APIError as exc:
        yield {
            "type": "error",
            "code": PERPLEXITY_UPSTREAM_ERROR_CODE,
            "data": PERPLEXITY_UPSTREAM_USER_MESSAGE,
        }
        await _log_perplexity_usage(
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=corr_id,
            model_name=model,
            status="error",
            error_message=str(exc),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            web_search_calls=0,
        )
        raise
    except Exception as exc:
        await _log_perplexity_usage(
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=corr_id,
            model_name=model,
            status="error",
            error_message=str(exc),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            web_search_calls=0,
        )
        raise
