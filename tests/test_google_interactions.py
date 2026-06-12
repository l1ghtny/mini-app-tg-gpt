import base64
import os
import sys
import types as pytypes
import uuid
import pytest
import aiohttp
from python_socks import ProxyType
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

    async def aclose(self):
        return None

class MockGenaiClient:
    def __init__(self, api_key=None, http_options=None):
        self.aio = MockAioClient()


class MockImage:
    def __init__(self, image_bytes: bytes):
        self.image_bytes = image_bytes


class MockGeneratedImage:
    def __init__(self, image_bytes: bytes | None = None, rai_filtered_reason: str | None = None):
        self.image = MockImage(image_bytes) if image_bytes is not None else None
        self.rai_filtered_reason = rai_filtered_reason


class MockGenerateImagesResponse:
    def __init__(self, image_bytes: bytes):
        self.generated_images = [MockGeneratedImage(image_bytes=image_bytes)]


async def _noop_async(**kwargs):
    return None

@pytest.mark.asyncio
async def test_google_interactions_stream_normalization(monkeypatch):
    # Setup test DB engine
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)
    monkeypatch.setattr(google_service, "engine", engine)
    
    # Enable API Key and mock Client
    monkeypatch.setattr(google_service.settings, "GEMINI_API_KEY", "test_key")
    monkeypatch.setattr(google_service.settings, "GEMINI_PROXY_URL", None)
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
    monkeypatch.setattr(google_service.settings, "GEMINI_PROXY_URL", None)
    monkeypatch.setattr(google_service, "_log_google_success_usage", _noop_async)

    interactions = MockInteractionsResource()

    class CapturingAioClient:
        def __init__(self):
            self.interactions = interactions

    class CapturingGenaiClient:
        def __init__(self, api_key=None, http_options=None):
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
async def test_google_auto_tool_choice_hands_off_image_generation_via_function_call(monkeypatch):
    monkeypatch.setattr(google_service.settings, "GEMINI_API_KEY", "test_key")
    monkeypatch.setattr(google_service.settings, "GEMINI_PROXY_URL", None)
    monkeypatch.setattr(google_service, "_log_google_success_usage", _noop_async)

    fake_png = b"\x89PNG\r\n\x1a\n\x00\x00fake"

    class FunctionCallStream:
        def __init__(self):
            self.events = [
                MockEvent("interaction.created", interaction=MockInteraction("router_intr")),
                MockEvent(
                    "step.start",
                    index=0,
                    step=pytypes.SimpleNamespace(
                        type="function_call",
                        id="call-1",
                        name="generate_image",
                        arguments={"prompt": "cinematic cyberpunk cat poster, neon lighting"},
                        signature="sig-1",
                    ),
                ),
                MockEvent("step.stop", index=0),
                MockEvent("interaction.completed", interaction=MockInteraction("router_intr", MockUsage(10, 3, 0))),
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

    class FollowupStream:
        def __init__(self):
            self.events = [
                MockEvent("interaction.created", interaction=MockInteraction("followup_intr")),
                MockEvent("step.start", index=1, step=MockStep("model_output")),
                MockEvent("step.delta", index=1, delta=MockDelta("text", text="Done.")),
                MockEvent("step.stop", index=1),
                MockEvent("interaction.completed", interaction=MockInteraction("followup_intr", MockUsage(4, 2, 0))),
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

    class CapturingInteractions:
        def __init__(self):
            self.calls = []

        async def create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return FunctionCallStream()
            return FollowupStream()

    class CapturingModels:
        def __init__(self):
            self.generate_images_calls = []

        async def generate_images(self, **kwargs):
            self.generate_images_calls.append(kwargs)
            return MockGenerateImagesResponse(fake_png)

    interactions = CapturingInteractions()
    image_models = CapturingModels()

    class CapturingAioClient:
        def __init__(self):
            self.interactions = interactions
            self.models = image_models

        async def aclose(self):
            return None

    class CapturingGenaiClient:
        def __init__(self, api_key=None, http_options=None):
            self.aio = CapturingAioClient()

    monkeypatch.setattr(google_service.genai, "Client", CapturingGenaiClient)

    events = []
    async for event in stream_normalized_google_response(
        [{"role": "user", "content": [{"type": "input_text", "text": "Draw a cinematic poster of a cat astronaut"}]}],
        model="gemini-3.1-flash-lite",
        instructions="You are a helpful assistant",
        tool_choice="auto",
        tools=[
            {"type": "web_search"},
            {"type": "image_generation", "model": "gemini-2.5-flash-image", "image_size": "2k"},
        ],
        user_id=None,
        conversation_id=None,
        request_id=str(uuid.uuid4()),
        assistant_message_id=None,
    ):
        events.append(event)

    assert interactions.calls
    assert interactions.calls[0]["model"] == "gemini-3.1-flash-lite"
    assert interactions.calls[0]["tools"][0]["type"] == "google_search"
    assert interactions.calls[0]["tools"][1]["type"] == "function"
    assert interactions.calls[1]["previous_interaction_id"] == "router_intr"
    assert interactions.calls[1]["input"][0]["type"] == "function_result"
    assert interactions.calls[1]["input"][0]["call_id"] == "call-1"

    assert image_models.generate_images_calls
    assert image_models.generate_images_calls[0]["model"] == "gemini-3.1-flash-image-preview"
    assert image_models.generate_images_calls[0]["prompt"] == "cinematic cyberpunk cat poster, neon lighting"
    assert image_models.generate_images_calls[0]["config"].image_size == "2K"

    assert any(ev.get("type") == "image.ready" for ev in events)
    assert any(ev.get("type") == "text.delta" and ev.get("text") == "Done." for ev in events)
    assert any(ev.get("type") == "done" for ev in events)
    image_ready = next(ev for ev in events if ev.get("type") == "image.ready")
    assert image_ready["data"] == base64.b64encode(fake_png).decode("ascii")


@pytest.mark.asyncio
async def test_google_explicit_image_generation_routes_only_the_function_tool(monkeypatch):
    monkeypatch.setattr(google_service.settings, "GEMINI_API_KEY", "test_key")
    monkeypatch.setattr(google_service.settings, "GEMINI_PROXY_URL", None)
    monkeypatch.setattr(google_service, "_log_google_success_usage", _noop_async)

    interactions = MockInteractionsResource()

    class CapturingAioClient:
        def __init__(self):
            self.interactions = interactions

    class CapturingGenaiClient:
        def __init__(self, api_key=None, http_options=None):
            self.aio = CapturingAioClient()

    monkeypatch.setattr(google_service.genai, "Client", CapturingGenaiClient)

    events = []
    async for event in stream_normalized_google_response(
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
        events.append(event)

    assert interactions.calls
    assert interactions.calls[0]["model"] == "gemini-3.1-flash-lite"
    assert len(interactions.calls[0]["tools"]) == 1
    assert interactions.calls[0]["tools"][0]["type"] == "function"
    assert any(ev.get("type") == "done" for ev in events)


@pytest.mark.asyncio
async def test_google_proxy_is_forwarded_to_genai_http_options(monkeypatch):
    monkeypatch.setattr(google_service.settings, "GEMINI_API_KEY", "test_key")
    monkeypatch.setattr(google_service.settings, "GEMINI_PROXY_URL", "socks5h://warp-proxy:1080")
    monkeypatch.setattr(google_service, "_module_available", lambda name: True)
    aiohttp_client = aiohttp.ClientSession()
    monkeypatch.setattr(google_service, "_build_google_aiohttp_client", lambda proxy_url: aiohttp_client)

    interactions = MockInteractionsResource()
    captured = {}

    class CapturingAioClient:
        def __init__(self):
            self.interactions = interactions

    class CapturingGenaiClient:
        def __init__(self, api_key=None, http_options=None):
            captured["api_key"] = api_key
            captured["http_options"] = http_options
            self.aio = CapturingAioClient()

    monkeypatch.setattr(google_service.genai, "Client", CapturingGenaiClient)

    async for _ in stream_normalized_google_response(
        [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
        model="gemini-3.1-flash-lite",
        instructions="You are a helpful assistant",
        tool_choice="auto",
        tools=[],
        user_id=None,
        conversation_id=None,
        request_id=str(uuid.uuid4()),
        assistant_message_id=None,
    ):
        pass

    assert captured["api_key"] == "test_key"
    assert captured["http_options"] is not None
    assert captured["http_options"].client_args["proxy"] == "socks5h://warp-proxy:1080"
    assert captured["http_options"].async_client_args["proxy"] == "socks5h://warp-proxy:1080"
    assert captured["http_options"].async_client_args["trust_env"] is True
    assert captured["http_options"].aiohttp_client is aiohttp_client
    assert aiohttp_client.closed is True


def test_google_socks_proxy_requires_socksio(monkeypatch):
    monkeypatch.setattr(google_service.settings, "GEMINI_PROXY_URL", "socks5h://warp-proxy:1080")
    monkeypatch.setattr(google_service, "_module_available", lambda name: False)

    with pytest.raises(RuntimeError, match="socksio"):
        google_service._build_google_http_options()


def test_google_socks_proxy_requires_aiohttp_socks(monkeypatch):
    monkeypatch.setattr(google_service.settings, "GEMINI_PROXY_URL", "socks5h://warp-proxy:1080")
    monkeypatch.setattr(google_service, "_module_available", lambda name: name == "socksio")

    with pytest.raises(RuntimeError, match="aiohttp-socks"):
        google_service._build_google_http_options()


@pytest.mark.parametrize(
    ("proxy_url", "expected_proxy_type", "expected_rdns"),
    [
        ("socks5://warp-proxy:1080", ProxyType.SOCKS5, False),
        ("socks5h://warp-proxy:1080", ProxyType.SOCKS5, True),
        ("socks4://warp-proxy:1080", ProxyType.SOCKS4, False),
        ("socks4a://warp-proxy:1080", ProxyType.SOCKS4, True),
    ],
)
def test_google_aiohttp_socks_aliases_map_to_connector_kwargs(
    monkeypatch,
    proxy_url,
    expected_proxy_type,
    expected_rdns,
):
    monkeypatch.setattr(google_service, "_module_available", lambda name: True)

    class FakeProxyConnector:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeClientSession:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setitem(sys.modules, "aiohttp", pytypes.SimpleNamespace(ClientSession=FakeClientSession))
    monkeypatch.setitem(sys.modules, "aiohttp_socks", pytypes.SimpleNamespace(ProxyConnector=FakeProxyConnector))

    client_session = google_service._build_google_aiohttp_client(proxy_url)

    connector = client_session.kwargs["connector"]
    assert client_session.kwargs["trust_env"] is True
    assert connector.kwargs["proxy_type"] == expected_proxy_type
    assert connector.kwargs["host"] == "warp-proxy"
    assert connector.kwargs["port"] == 1080
    assert connector.kwargs["rdns"] is expected_rdns


def test_google_image_models_map_low_thinking_to_minimal():
    config = _generation_config_for_request(
        model="gemini-3.1-flash-image-preview",
        thinking_enabled=False,
        reasoning_effort=None,
        image_size="1k",
    )

    assert config["thinking_level"] == "low"
    assert config["thinking_summaries"] == "auto"
    assert config["image_config"] == {"image_size": "1K"}
