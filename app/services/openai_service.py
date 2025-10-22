import json
from pprint import pprint

from openai import AsyncOpenAI, AuthenticationError, NotFoundError
from typing import AsyncGenerator, List


from app.schemas.chat import Message

client = AsyncOpenAI()


# TODO: add conversation_id from openai API so that it can be used to identify the conversation
async def get_openai_response(
        messages: List[Message],
        model: str,
        instructions: str = "You are a helpful assistant.",
        tool_choice: str = "auto",
) -> AsyncGenerator[str, None]:
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
            # pprint(event)
            event_type = event.type

            if event_type == 'response.output_item.added' and hasattr(event, 'item') and event.item.type == 'reasoning':
                yield json.dumps({"type": "status", "data": "thinking"}) + "\n"

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
        yield json.dumps({"type": "status", "data": "complete"}) + "\n"

    except AuthenticationError as e:
        yield json.dumps({"type": "error", "data": "OpenAI authentication failed. Check API key."}) + "\n"
    except NotFoundError as e:
        yield json.dumps({"type": "error", "data": "Model not found. Please check the model name."}) + "\n"


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
