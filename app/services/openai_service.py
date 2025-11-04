import asyncio
from typing import AsyncGenerator, List, Optional, Dict, Any
import json
from pprint import pprint
import uuid

from openai import AsyncOpenAI
from openai import AuthenticationError, NotFoundError
from openai.types.responses import FileSearchToolParam, ResponseImageGenCallGeneratingEvent, \
    ResponseImageGenCallInProgressEvent, ResponseImageGenCallPartialImageEvent
from openai.types.responses.tool_param import ImageGeneration, WebSearchTool
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import TokenUsage
from app.db.database import get_session
from app.redis.settings import settings
from app.schemas.chat import Message
from app.services.background.save_openai_usage import log_usage
from app.services.pricing_service import PricingService

STYLE_GUIDE = (
    "Always format replies in Markdown:\n"
    "- Use proper headings for sections (##, ###).\n"
    "- Use bullet lists with '-' and numbered lists with '1.' (not '1)')\n"
    "- Use fenced code blocks for code.\n"
    "- Use standard [text](url) links.\n"
)

client = AsyncOpenAI()


async def stream_normalized_openai_response(
    messages: List["Message"],
    model: Optional[str] = 'gpt-5-nano',
    *,
    instructions: Optional[str] = "You are a helpful assistant.",
    tool_choice: Optional[str] = "auto",
    user_id: Optional[uuid.UUID] = None,
    conversation_id: Optional[uuid.UUID] = None,
    request_id: Optional[str] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Adapts OpenAI Responses API stream into part-oriented events.
    """
    input_tokens = output_tokens = reasoning_tokens = 0
    web_search_calls = images_generated = 0
    corr_id = request_id or str(uuid.uuid4())

    # coalescing buffer for text by content_index
    text_buf: Dict[int, str] = {}
    last_flush: Dict[int, float] = {}

    async def flush(index: int, force=False):
        if not text_buf.get(index):
            return
        now = asyncio.get_running_loop().time()
        if force or (now - last_flush.get(index, 0)) * 1000 >= settings.COALESCE_MS:
            yield {"type": "text.delta", "index": index, "text": text_buf[index]}
            text_buf[index] = ""
            last_flush[index] = now

    try:
        response = await client.responses.create(
            model=model,
            tools=[ImageGeneration(type="image_generation", model="gpt-image-1-mini", quality="medium"), WebSearchTool(type="web_search")],
            tool_choice=tool_choice,
            instructions=instructions,
            input=messages,
            stream=True,
            service_tier="flex"
        )

        # This maps OpenAI event names to our normalised events
        seen_text_part_started: Dict[int, bool] = {}
        try:
            async for event in response:
                pprint(event)
                et = event.type

                # Reasoning “thinking” status
                if et == "response.output_item.added" and getattr(event, "item", None) and event.item.type == "reasoning":
                    yield {"type": "status", "stage": "thinking"}
                    continue

                # Start of the assistant message text part (Responses API uses content parts)
                if et == "response.content_part.added":
                    # content_index numbers the part; map types
                    i = event.content_index
                    p = event.part
                    if getattr(p, "type", None) in ("output_text", "text", "output_text_delta"):
                        if not seen_text_part_started.get(i):
                            seen_text_part_started[i] = True
                            yield {"type": "part.start", "index": i, "content_type": "text"}
                            # init buffers
                            text_buf.setdefault(i, ""); last_flush.setdefault(i, 0)
                    # You can add branches for other part types here if Responses starts sending them
                    continue

                # Text token deltas
                if et == "response.output_text.delta":
                    i = event.content_index
                    if not seen_text_part_started.get(i):
                        seen_text_part_started[i] = True
                        yield {"type": "part.start", "index": i, "content_type": "text"}
                        text_buf.setdefault(i, ""); last_flush.setdefault(i, 0)
                    text_buf[i] += event.delta
                    # try flushing (coalesce)
                    async for out in flush(i):
                        yield out
                    continue

                # End of a text part
                if et == "response.output_text.done":
                    i = event.content_index
                    # force-flush remainder
                    if text_buf.get(i):
                        yield {"type": "text.delta", "index": i, "text": text_buf[i]}
                        text_buf[i] = ""
                    yield {"type": "text.done", "index": i}
                    continue

                # Web search tool lifecycle (keep as UI-only status + count)
                if et in ("response.web_search_call.in_progress", "response.web_search_call.searching"):
                    yield {"type": "status", "stage": "web_search.in_progress"}
                    continue
                if et == "response.web_search_call.completed":
                    web_search_calls += 1
                    yield {"type": "status", "stage": "web_search.completed"}
                    continue

                # Image generation result (Responses emits this as an “image_generation_call” output item)
                if et == "response.output_item.done" and getattr(event, "item", None) and event.item.type == "image_generation_call":
                    if getattr(event.item, "result", None):
                        images_generated += 1
                        # If you base64-save: format=b64. Better: upload -> url.
                        yield {"type": "part.start", "index": 999, "content_type": "image"}  # choose the next ordinal if mixed
                        yield {
                            "type": "image.ready",
                            "index": 999,
                            "format": "b64",
                            "data": event.response.base64_encoded_image_data,
                        }
                    continue

                elif event == ResponseImageGenCallGeneratingEvent | ResponseImageGenCallInProgressEvent | ResponseImageGenCallPartialImageEvent:
                    yield {"type": "status", "stage": "image_generation.in_progress"}
                    continue


                # Stream lifecycle: completed and usage
                if et in ("response.completed", "response.completed.successfully"):
                    usage = event.response.usage
                    if usage:
                        input_tokens = usage.get("input_tokens") or input_tokens
                        output_tokens = usage.output_tokens or output_tokens
                        if input_tokens:
                            input_tokens_details = usage.input_tokens_details
                            print(f'\n\nINPUT TOKEN DETAILS: {input_tokens_details.cached_tokens}\n\n')
                            cached_tokens = input_tokens_details.cached_tokens or 0
                        else:
                            input_tokens_details = 0
                        if output_tokens:
                            output_tokens_details = usage.output_tokens_details
                            reasoning_tokens = output_tokens_details.get("reasoning_tokens") or reasoning_tokens
                            web_search_calls = output_tokens_details.get("web_search_calls") or web_search_calls
                            print(f'\n\nOUTPUT TOKEN DETAILS: {output_tokens_details.reasoning_tokens}\n\n')
                        else:
                            output_tokens_details = 0
                    # force-flush any lingering text buffers
                    for i, buf in list(text_buf.items()):
                        if buf:
                            yield {"type": "text.delta", "index": i, "text": buf}
                            yield {"type": "text.done", "index": i}
                            text_buf[i] = ""
                    yield {"type": "done"}
                    continue
        except Exception as e:
            yield {"type": "error", "data": str(e)}
            raise

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
        yield {"type": "error", "data": "OpenAI authentication failed. Check API key."}
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
        yield {"type": "error", "data": "Model not found. Please check the model name."}
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
