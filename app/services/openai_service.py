import json
from pprint import pprint

from openai import AsyncOpenAI, AuthenticationError, NotFoundError
from typing import AsyncGenerator, List

from openai.types.responses.tool_param import WebSearchTool

from app.schemas.chat import Message

client = AsyncOpenAI()


# TODO: add conversation_id from openai API so that it can be used to identify the conversation
async def get_openai_response(messages: List[Message]) -> AsyncGenerator[str, None]:
    try:
        response = await client.responses.create(
            model="gpt-5-mini",
            tools=[{"type": "web_search"}, {"type": "image_generation"}],
            tool_choice="auto",
            instructions="You are a helpful assistant.",
            input=messages,
            stream=True
        )

        async for event in response:
            pprint(event)
            event_type = event.type

            # Event: Text chunk is received
            if event_type == 'response.output_text.delta':
                yield json.dumps({"type": "text_chunk", "data": event.delta}) + "\n"

            # Event: Web search has started
            elif event_type == 'response.web_search_call.searching':
                yield json.dumps({"type": "tool_status", "tool": "web_search", "status": "Searching the web..."}) + "\n"

            # Event: Image generation has started
            elif event_type == 'response.image_generation_call.generating':
                yield json.dumps(
                    {"type": "tool_status", "tool": "image_generation", "status": "Generating image..."}) + "\n"

            # Event: An image generation call is complete and the result is available
            elif event_type == 'response.output_item.done' and hasattr(event,
                                                                       'item') and event.item.type == 'image_generation_call':
                if event.item.result and event.item.result and event.item.result:
                    # The image is sent as a Base64 string
                    b64_data = event.item.result
                    # We'll construct the data URL on the frontend
                    yield json.dumps({"type": "image", "format": "b64_json", "data": b64_data}) + "\n"

    except AuthenticationError as e:
        yield json.dumps({"type": "error", "data": "OpenAI authentication failed. Check API key."}) + "\n"
    except NotFoundError as e:
        yield json.dumps({"type": "error", "data": "Model not found. Please check the model name."}) + "\n"
