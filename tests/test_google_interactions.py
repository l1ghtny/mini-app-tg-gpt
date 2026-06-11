import os
import uuid
import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.models import AppUser, Conversation, TokenUsage
from app.services.google_service import _generation_config_for_request, stream_normalized_google_response
import app.services.google_service as google_service

class MockUsage:
    def __init__(self, input_tokens=10, output_tokens=20, thought_tokens=5):
        self.total_input_tokens = input_tokens
        self.total_output_tokens = output_tokens
        self.total_thought_tokens = thought_tokens

class MockInteraction:
    def __init__(self, id="mock_intr_id", usage=None):
        self.id = id
        self.usage = usage or MockUsage()

class MockStep:
    def __init__(self, type):
        self.type = type

class MockDelta:
    def __init__(self, type, text=None, content=None, data=None):
        self.type = type
        self.text = text
        self.content = content
        self.data = data

class MockEvent:
    def __init__(self, event_type, index=None, interaction=None, step=None, delta=None, error=None):
        self.event_type = event_type
        self.index = index
        self.interaction = interaction
        self.step = step
        self.delta = delta
        self.error = error

class MockStream:
    def __init__(self):
        self.events = [
            MockEvent("interaction.created", interaction=MockInteraction("mock_intr_1")),
            MockEvent("step.start", index=0, step=MockStep("thought")),
            MockEvent("step.delta", index=0, delta=MockDelta("thought_summary", content="Thinking...")),
            MockEvent("step.stop", index=0),
            MockEvent("step.start", index=1, step=MockStep("model_output")),
            MockEvent("step.delta", index=1, delta=MockDelta("text", text="Hello world")),
            MockEvent("step.stop", index=1),
            MockEvent("interaction.completed", interaction=MockInteraction("mock_intr_1", MockUsage(12, 24, 6)))
        ]

    def __aiter__(self):
        self.index = 0
        return self

    async def __anext__(self):
        if self.index >= len(self.events):
            raise StopAsyncIteration
        event = self.events[self.index]
        self.index += 1
        return event

class MockInteractionsResource:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return MockStream()

class MockAioClient:
    def __init__(self):
        self.interactions = MockInteractionsResource()

class MockGenaiClient:
    def __init__(self, api_key=None):
        self.aio = MockAioClient()

@pytest.mark.asyncio
async def test_google_interactions_stream_normalization(monkeypatch):
    # Setup test DB engine
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)
    monkeypatch.setattr(google_service, "engine", engine)
    
    # Enable API Key and mock Client
    monkeypatch.setattr(google_service.settings, "GEMINI_API_KEY", "test_key")
    monkeypatch.setattr(google_service.genai, "Client", MockGenaiClient)
    
    # Create test user/convo
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=741000001)
        session.add(user)
        await session.commit()
        await session.refresh(user)

        conversation = Conversation(
            user_id=user.id,
            title="Google Stream Normalization Test",
            model="gemini-3.5-flash",
        )
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)

    messages = [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}]
    
    events = []
    async for event in stream_normalized_google_response(
        messages,
        model="gemini-3.5-flash",
        instructions="You are a helpful assistant",
        tool_choice="auto",
        tools=[],
        user_id=user.id,
        conversation_id=conversation.id,
        request_id=str(uuid.uuid4()),
        assistant_message_id=None,
    ):
        events.append(event)
        
    # Check that events were properly yielded and translated
    assert any(ev.get("type") == "response.meta" and ev.get("interaction_id") == "mock_intr_1" for ev in events)
    assert any(ev.get("type") == "status" and ev.get("stage") == "thinking" and ev.get("status") == "active" for ev in events)
    assert any(ev.get("type") == "reasoning.summary.delta" and ev.get("delta") == "Thinking..." for ev in events)
    assert any(ev.get("type") == "text.delta" and ev.get("text") == "Hello world" for ev in events)
    assert any(ev.get("type") == "status" and ev.get("stage") == "completed" for ev in events)
    assert any(ev.get("type") == "done" for ev in events)
    
    # Verify token usage is logged to DB
    async with AsyncSession(engine, expire_on_commit=False) as session:
        usage = (await session.exec(select(TokenUsage).where(
            TokenUsage.conversation_id == conversation.id,
            TokenUsage.provider == "google"
        ))).first()
        assert usage is not None
        assert usage.input_tokens == 12
        assert usage.output_tokens == 24
        assert usage.reasoning_tokens == 6
        assert usage.status == "success"


@pytest.mark.asyncio
async def test_google_auto_tool_choice_keeps_text_model(monkeypatch):
    monkeypatch.setattr(google_service.settings, "GEMINI_API_KEY", "test_key")

    interactions = MockInteractionsResource()

    class CapturingAioClient:
        def __init__(self):
            self.interactions = interactions

    class CapturingGenaiClient:
        def __init__(self, api_key=None):
            self.aio = CapturingAioClient()

    monkeypatch.setattr(google_service.genai, "Client", CapturingGenaiClient)

    events = []
    async for event in stream_normalized_google_response(
        [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
        model="gemini-3.1-flash-lite",
        instructions="You are a helpful assistant",
        tool_choice="auto",
        tools=[
            {"type": "web_search"},
            {"type": "image_generation", "model": "gemini-2.5-flash-image", "image_size": "1k"},
        ],
        user_id=None,
        conversation_id=None,
        request_id=str(uuid.uuid4()),
        assistant_message_id=None,
    ):
        events.append(event)

    assert interactions.calls
    assert interactions.calls[0]["model"] == "gemini-3.1-flash-lite"
    assert "response_modalities" not in interactions.calls[0]
    assert any(ev.get("type") == "done" for ev in events)


@pytest.mark.asyncio
async def test_google_explicit_image_generation_uses_image_model(monkeypatch):
    monkeypatch.setattr(google_service.settings, "GEMINI_API_KEY", "test_key")

    interactions = MockInteractionsResource()

    class CapturingAioClient:
        def __init__(self):
            self.interactions = interactions

    class CapturingGenaiClient:
        def __init__(self, api_key=None):
            self.aio = CapturingAioClient()

    monkeypatch.setattr(google_service.genai, "Client", CapturingGenaiClient)

    async for _ in stream_normalized_google_response(
        [{"role": "user", "content": [{"type": "input_text", "text": "draw a cat"}]}],
        model="gemini-3.1-flash-lite",
        instructions="You are a helpful assistant",
        tool_choice={"type": "allowed_tools", "mode": "required", "tools": [{"type": "image_generation"}]},
        tools=[
            {"type": "web_search"},
            {"type": "image_generation", "model": "gemini-2.5-flash-image", "image_size": "2k"},
        ],
        user_id=None,
        conversation_id=None,
        request_id=str(uuid.uuid4()),
        assistant_message_id=None,
    ):
        pass

    assert interactions.calls
    assert interactions.calls[0]["model"] == "gemini-2.5-flash-image"
    assert interactions.calls[0]["response_modalities"] == ["text", "image"]
    assert interactions.calls[0]["generation_config"]["image_config"] == {"image_size": "2K"}


def test_google_image_models_map_low_thinking_to_minimal():
    config = _generation_config_for_request(
        model="gemini-3.1-flash-image-preview",
        thinking_enabled=False,
        reasoning_effort=None,
        image_size="1k",
    )

    assert config["thinking_level"] == "minimal"
    assert config["thinking_summaries"] == "auto"
    assert config["image_config"] == {"image_size": "1K"}
