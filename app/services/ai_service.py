import uuid
from typing import Any, AsyncGenerator, Iterable, Optional

from openai.types.responses import FileSearchToolParam, WebSearchToolParam
from openai.types.responses.tool import CodeInterpreter, ImageGeneration

from app.services.google_service import stream_normalized_google_response
from app.services.model_registry import get_text_model_provider
from app.services.openai_service import stream_normalized_openai_response
from app.services.perplexity_service import stream_normalized_perplexity_response


def _resolve_openai_reasoning_summary(
    requested_summary: Optional[str],
    thinking_enabled: bool | None,
) -> Optional[str]:
    # Legacy behavior: when no explicit toggle is provided, keep requesting
    # concise summaries so existing OpenAI UX remains unchanged.
    if thinking_enabled is None:
        return requested_summary
    if not thinking_enabled:
        return None
    # thinking_enabled is True
    return requested_summary or "extended"


def _resolve_openai_reasoning_effort(
    thinking_enabled: bool | None,
) -> str | None:
    # Keep legacy behavior when the toggle is absent.
    if thinking_enabled is None:
        return None
    if not thinking_enabled:
        return None
    # When user explicitly enables thinking on OpenAI, request non-trivial effort.
    return "medium"


async def stream_normalized_ai_response(
    messages: list[dict[str, Any]],
    model: Optional[str] = "gpt-5.4-nano",
    *,
    instructions: Optional[str] = "You are a helpful assistant.",
    tool_choice: Any = "auto",
    tools: Optional[Iterable[FileSearchToolParam | WebSearchToolParam | CodeInterpreter | ImageGeneration]] = None,
    user_id: Optional[uuid.UUID] = None,
    conversation_id: Optional[uuid.UUID] = None,
    request_id: Optional[str] = None,
    assistant_message_id: Optional[uuid.UUID] = None,
    reasoning_summary: Optional[str] = "detailed",
    previous_response_id: Optional[str] = None,
    previous_interaction_id: Optional[str] = None,
    fallback_messages: Optional[list[dict[str, Any]]] = None,
    thinking_enabled: bool | None = None,
    reasoning_effort: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    provider = get_text_model_provider(model or "gpt-5.4-nano")
    if provider == "google":
        async for event in stream_normalized_google_response(
            messages,
            model or "gpt-5.4-nano",
            instructions=instructions,
            tool_choice=tool_choice,
            tools=tools,
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=request_id,
            assistant_message_id=assistant_message_id,
            previous_interaction_id=previous_interaction_id,
            thinking_enabled=thinking_enabled,
            reasoning_effort=reasoning_effort,
        ):
            yield event
        return

    if provider == "perplexity":
        async for event in stream_normalized_perplexity_response(
            messages,
            model or "sonar",
            instructions=instructions,
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=request_id,
        ):
            yield event
        return

    openai_reasoning_summary = _resolve_openai_reasoning_summary(
        requested_summary=reasoning_summary,
        thinking_enabled=thinking_enabled,
    )
    openai_reasoning_effort = _resolve_openai_reasoning_effort(
        thinking_enabled=thinking_enabled,
    )

    async for event in stream_normalized_openai_response(
        messages,
        model,
        instructions=instructions,
        tool_choice=tool_choice,
        tools=tools,
        user_id=user_id,
        conversation_id=conversation_id,
        request_id=request_id,
        assistant_message_id=assistant_message_id,
        reasoning_summary=openai_reasoning_summary,
        reasoning_effort=openai_reasoning_effort,
        previous_response_id=previous_response_id,
        fallback_messages=fallback_messages,
    ):
        yield event
