import asyncio
import random
import uuid
from typing import AsyncGenerator, List, Optional, Dict, Any, Literal, Iterable

import openai
from openai import AsyncOpenAI, BadRequestError
from openai import AuthenticationError, NotFoundError
from openai.types.responses import FileSearchToolParam, ResponseImageGenCallGeneratingEvent, \
    ResponseImageGenCallInProgressEvent, ResponseImageGenCallPartialImageEvent, ToolChoiceAllowedParam, \
    ToolChoiceTypesParam, WebSearchToolParam
from openai.types.responses.tool import CodeInterpreter, WebSearchTool, ImageGeneration
from openai.types.responses.tool_param import ImageGeneration
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.database import get_session, engine
from app.redis.settings import settings
from app.schemas.chat import Message
from app.services.background.save_openai_usage import log_usage

STYLE_GUIDE = (
    "Format replies in Markdown:\n"
    "- Use proper headings for sections (##, ###).\n"
    "- Use bullet lists with '-' and numbered lists with '1.' (not '1)')\n"
    "- Use fenced code blocks for code.\n"
    "- Use standard [text](url) links.\n"
    "Only use headings bullet lists and others when it is applicable"
)

client = AsyncOpenAI()


default_tools = [
    ImageGeneration(type="image_generation", model="gpt-image-1-mini", quality="medium"),
    WebSearchTool(type="web_search")
]

def _is_openai_image_download_timeout(exc: Exception) -> bool:
    """
    OpenAI returns 400 invalid_request_error when it can't download the image URL in time.
    Example message:
      "Timeout while downloading https://....png."
    """
    s = str(exc)
    return ("Timeout while downloading" in s) and ("param" in s and "'url'" in s or "param': 'url'" in s)


async def _retry_delay_s(attempt: int, base: float = 0.8, cap: float = 8.0) -> float:
    # exponential backoff with jitter
    exp = min(cap, base * (2 ** attempt))
    return exp + random.random() * 0.25


async def stream_normalized_openai_response(
    messages: List["Message"],
    model: Optional[str] = 'gpt-5-nano',
    *,
    instructions: Optional[str] = "You are a helpful assistant.",
    tool_choice: Literal["none", "auto", "required"] | ToolChoiceAllowedParam | ToolChoiceTypesParam = "auto",
    tools: Optional[Iterable[FileSearchToolParam | WebSearchToolParam | CodeInterpreter | ImageGeneration]] = None,
    user_id: Optional[uuid.UUID] = None,
    conversation_id: Optional[uuid.UUID] = None,
    request_id: Optional[str] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Adapts OpenAI Responses API stream into part-oriented events.
    """

    if not tools:
        tools = default_tools

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
        response = None
        max_openai_retries = 3

        for attempt in range(max_openai_retries):
            try:
                response = await client.responses.create(
                    model=model,
                    tools=tools,
                    tool_choice=tool_choice,
                    instructions=instructions + STYLE_GUIDE,
                    input=messages,
                    stream=True,
                    service_tier="default",
                )
                break
            except openai.BadRequestError as e:
                if _is_openai_image_download_timeout(e) and attempt < max_openai_retries - 1:
                    await asyncio.sleep(await _retry_delay_s(attempt))
                    continue
                raise

        # This maps OpenAI event names to our normalised events
        seen_text_part_started: Dict[int, bool] = {}
        try:
            async for event in response:
                # pprint(event)
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
                        yield {"type": "part.start", "index": event.output_index, "content_type": "image"}  # choose the next ordinal if mixed
                        ## There are more parameters you can extract from the ImageGeneration (event.item) object, like: status, quality, revised_prompt, background, output_format
                        yield {
                            "type": "image.ready",
                            "index": event.output_index,
                            "format": "b64",
                            "data": event.item.result,
                        }
                    continue

                elif event == ResponseImageGenCallGeneratingEvent | ResponseImageGenCallInProgressEvent | ResponseImageGenCallPartialImageEvent:
                    yield {"type": "status", "stage": "image_generation.in_progress"}
                    continue


                # Stream lifecycle: completed and usage
                if et in ("response.completed", "response.completed.successfully"):
                    usage = event.response.usage
                    if usage:
                        input_tokens = usage.input_tokens or input_tokens
                        output_tokens = usage.output_tokens or output_tokens
                        if input_tokens:
                            input_tokens_details = usage.input_tokens_details
                            print(f'\n\nINPUT TOKEN DETAILS: {input_tokens_details.cached_tokens}\n\n')
                            cached_tokens = input_tokens_details.cached_tokens or 0
                        else:
                            input_tokens_details = 0
                        if output_tokens:
                            output_tokens_details = usage.output_tokens_details
                            reasoning_tokens = getattr(output_tokens_details, 'reasoning_tokens', None) or reasoning_tokens
                            web_search_calls = getattr(output_tokens_details, 'websearch_details', None) or web_search_calls
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
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                web_search_calls=web_search_calls,
                images_generated=images_generated,
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
        title = response.choices[0].message.content.strip().strip('"').strip('.')
        return title if title else "New Chat"
    except Exception as e:
        print(f"Error generating title: {e}")
        return "New Chat" # Fallback title
