import uuid
from typing import Any, AsyncGenerator, Iterable, Optional

from openai.types.responses import FileSearchToolParam, WebSearchToolParam
from openai.types.responses.tool import CodeInterpreter, ImageGeneration

from app.services.google_service import stream_normalized_google_response
from app.services.model_registry import get_text_model_provider
from app.services.openai_service import stream_normalized_openai_response


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
    reasoning_summary: Optional[str] = "concise",
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
            previous_interaction_id=previous_interaction_id,
            thinking_enabled=thinking_enabled,
            reasoning_effort=reasoning_effort,
        ):
            yield event
        return

    async for event in stream_normalized_openai_response(
        messages,
        model,
        instructions=instructions,
        tool_choice=tool_choice,
        tools=tools,
        user_id=user_id,
        conversation_id=conversation_id,
        request_id=request_id,
        reasoning_summary=reasoning_summary,
        previous_response_id=previous_response_id,
        fallback_messages=fallback_messages,
    ):
        yield event
