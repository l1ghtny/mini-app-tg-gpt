from typing import AsyncGenerator, List, Optional
import json
from pprint import pprint
import uuid

from openai import AsyncOpenAI
from openai import AuthenticationError, NotFoundError
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import TokenUsage
from app.db.database import get_session
from app.services.pricing_service import PricingService

STYLE_GUIDE = (
    "Always format replies in Markdown:\n"
    "- Use proper headings for sections (##, ###).\n"
    "- Use bullet lists with '-' and numbered lists with '1.' (not '1)')\n"
    "- Use fenced code blocks for code.\n"
    "- Use standard [text](url) links.\n"
)

client = AsyncOpenAI()


async def stream_openai_response(
        messages: List["Message"],
        model: str,
        instructions: Optional[str] = "You are a helpful assistant.",
        tool_choice: Optional[str] = "auto",
        *,
        user_id: Optional[uuid.UUID] = None,
        conversation_id: Optional[uuid.UUID] = None,
        request_id: Optional[str] = None
) -> AsyncGenerator[str, None]:
    input_tokens = 0
    output_tokens = 0
    reasoning_tokens = 0
    web_search_calls = 0
    images_generated = 0

    corr_id = request_id or str(uuid.uuid4())

    print(f'MESSAGES: {messages}')

    try:
        response = await client.responses.create(
            model=model,
            tools=[{"type": "web_search"}, {"type": "image_generation"}],
            tool_choice=tool_choice,
            instructions=instructions,
            input=messages,
            stream=True
        )

        async for event in response:
            pprint(event)
            event_type = event.type

            if event_type == 'response.output_item.added' and hasattr(event, 'item') and event.item.type == 'reasoning':
                yield json.dumps({"type": "status", "data": "thinking"}) + "\n"

            if event_type == 'response.output_text.delta':
                yield json.dumps({"type": "text_chunk", "data": event.delta}) + "\n"

            elif event_type == 'response.web_search_call.in_progress':
                yield json.dumps({"type": "tool_status", "tool": "web_search", "status": "Searching the web..."}) + "\n"

            elif event_type == 'response.web_search_call.searching':
                yield json.dumps({"type": "tool_status", "tool": "web_search", "status": "Searching the web..."}) + "\n"

            elif event_type == 'response.web_search_call.completed':
                web_search_calls += 1
                yield json.dumps({"type": "tool_status", "tool": "web_search", "status": "Found sources"}) + "\n"

            elif event_type == 'response.image_generation_call.generating':
                yield json.dumps({"type": "tool_status", "tool": "image_generation", "status": "Generating image..."}) + "\n"

            elif event_type == 'response.output_item.done' and hasattr(event, 'item') and event.item.type == 'image_generation_call':
                if getattr(event.item, "result", None):
                    images_generated += 1
                    yield json.dumps({"type": "image", "format": "b64_json", "data": event.item.result}) + "\n"

            elif event_type in ('response.completed', 'response.completed.successfully'):
                usage = getattr(event, "usage", None)
                if usage:
                    input_tokens = getattr(usage, "input_tokens", input_tokens) or input_tokens
                    output_tokens = getattr(usage, "output_tokens", output_tokens) or output_tokens
                    reasoning_tokens = getattr(usage, "reasoning_tokens", reasoning_tokens) or reasoning_tokens

        yield json.dumps({"type": "status", "data": "complete"}) + "\n"

        async for session in get_session():
            await log_usage(
                session,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=corr_id,
                provider="openai",
                model_name=model,
                status="success",
                error_message=None,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                web_search_calls=web_search_calls,
                images_generated=images_generated,
            )
            break

    except AuthenticationError:
        yield json.dumps({"type": "error", "data": "OpenAI authentication failed. Check API key."}) + "\n"
        async for session in get_session():
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
            break
    except NotFoundError:
        yield json.dumps({"type": "error", "data": "Model not found. Please check the model name."}) + "\n"
        async for session in get_session():
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
            break


async def generate_conversation_title(first_message: str) -> str:
    try:
        # We use a simple, fast model for this non-streaming task.
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are an expert at creating short, concise titles. Summarize the user's message in 5 words or less. Do not use quotation marks or punctuation."},
                {"role": "user", "content": first_message}
            ],
            temperature=0,
            max_tokens=20,
        )
        print(response)
        title = response.choices[0].message.content.strip().strip('"').strip('.')
        return title if title else "New Chat"
    except Exception as e:
        print(f"Error generating title: {e}")
        return "New Chat" # Fallback title
