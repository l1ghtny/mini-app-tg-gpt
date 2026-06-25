import uuid
from typing import Any, AsyncGenerator, Optional

from openai import APIError, AsyncOpenAI, AuthenticationError, NotFoundError
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.db.database import engine
from app.services.background.save_openai_usage import log_usage

PERPLEXITY_UPSTREAM_ERROR_CODE = "PERPLEXITY_UPSTREAM_UNAVAILABLE"
PERPLEXITY_UPSTREAM_USER_MESSAGE = "Sorry, Perplexity Search has some issues on their end. Please try again in a moment."

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
) -> None:
    async with AsyncSession(engine, expire_on_commit=False) as db_session:
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


async def stream_normalized_perplexity_response(
    messages: list[dict[str, Any]],
    model: str,
    *,
    instructions: Optional[str],
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
                    "search_context_size": settings.PERPLEXITY_SEARCH_CONTEXT_SIZE,
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
