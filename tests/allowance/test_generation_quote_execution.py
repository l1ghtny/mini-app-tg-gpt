import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from fastapi import BackgroundTasks
import pytest
import pytest_asyncio
from sqlmodel import select

from app.api import chat_helpers as chat
from app.core.config import settings
from app.db.allowance import ProviderAttempt
from app.db.models import ChatFolder, Conversation, Message, MessageContent
from app.schemas.chat import NewMessageRequest
from app.services import allowance, allowance_chat, allowance_context, openai_service
from app.services import shared_chat_provider as provider
from app.services.allowance_policy import (
    FLARE,
    LUNA,
    MAX_TOOL_QUERY_BYTES,
    image_budget,
    text_tokens,
)


@pytest_asyncio.fixture
async def send_case(db, monkeypatch):
    engine, session, user = db
    async with engine.begin() as conn:
        for model in (ChatFolder, Conversation, Message, MessageContent):
            await conn.run_sync(lambda c, m=model: m.__table__.create(c))
    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_GENERATION_V2_ENABLED", True)
    monkeypatch.setattr(provider, "engine", engine)
    monkeypatch.setattr(allowance_context, "engine", engine)
    for name in (
        "queue_message_reindex",
        "queue_projection_refresh",
        "_track_message_metrics",
    ):
        monkeypatch.setattr(chat, name, AsyncMock())
    monkeypatch.setattr(chat, "_get_idempotency_response", AsyncMock(return_value=None))
    monkeypatch.setattr(
        chat, "_snapshot_conversation_documents", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        chat, "count_conversation_pending_indexing_documents", AsyncMock(return_value=0)
    )
    monkeypatch.setattr(
        chat, "list_conversation_ready_vector_store_ids", AsyncMock(return_value=[])
    )

    async def reserve_legacy(*args, **kwargs):
        await session.commit()

    monkeypatch.setattr(chat, "reserve_request", reserve_legacy)
    queued = Mock()
    monkeypatch.setattr(chat, "_queue_generation", queued)
    conversation = Conversation(
        user_id=user.id, model=LUNA, tool_choice=[], image_quality="low", folder=None
    )
    session.add(conversation)
    await session.commit()
    conversation = await chat._load_conversation_for_user(
        session, conversation.id, user.id
    )
    return session, user, conversation, queued


def synthetic_client(monkeypatch, outputs, image_response=None):
    payloads = []

    class Stream:
        def __init__(self, output, usage):
            self.output, self.usage = output, usage

        def __aiter__(self):
            async def events():
                yield SimpleNamespace(
                    type="response.completed",
                    response=SimpleNamespace(
                        id=f"synthetic-{len(payloads)}",
                        status="completed",
                        usage=self.usage,
                        output=[SimpleNamespace(model_dump=lambda **kw: self.output)],
                    ),
                )

            return events()

        async def close(self):
            pass

    async def create(**kwargs):
        output = outputs[len(payloads)]
        payloads.append(kwargs)
        count = text_tokens(json.dumps(output, ensure_ascii=False))
        assert count < kwargs["max_output_tokens"]
        return Stream(output, {"input_tokens": 100, "output_tokens": count})

    images = SimpleNamespace(
        generate=AsyncMock(return_value=image_response), edit=AsyncMock()
    )
    client = SimpleNamespace(responses=SimpleNamespace(create=create), images=images)
    client.with_options = lambda **kw: client
    monkeypatch.setattr(provider, "client", client)
    monkeypatch.setattr(openai_service, "client", client)
    return payloads, images


def answer(text):
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }


async def send_and_execute(session, user, conversation, queued, request):
    quote = await allowance_chat.estimate(session, user, conversation, request)
    request.estimate_reference = quote["estimate_reference"]
    request.spend_limit_units = quote["ceiling_units"]
    await chat.handle_create_message(
        conversation_id=conversation.id,
        request=request,
        background_tasks=BackgroundTasks(),
        session=session,
        current_user=user,
        bus=SimpleNamespace(set=AsyncMock()),
    )
    args = queued.call_args.kwargs
    events = [
        event
        async for event in provider.stream_shared_response(
            args["history_for_openai"],
            args["model"],
            instructions=args["instructions"],
            tools=args["tools"],
            tool_choice=args["tool_choice"],
            user_id=args["user_id"],
            request_id=args["request_id"],
            conversation_id=args["conversation_id"],
            reasoning_effort=args["reasoning_effort"],
            thinking_enabled=args["thinking_enabled"],
        )
    ]
    assert events[-1] == {"type": "done"}
    await allowance.settle(session, user.id, request.client_request_id, success=True)
    return quote, args, events


@pytest.mark.asyncio
async def test_zero_paid_balance_luna_quote_send_and_provider_capacity_match(
    send_case, monkeypatch
):
    session, user, conversation, queued = send_case
    account = await allowance.account(session, user.id)
    account.spent = account.granted
    await session.commit()
    payloads, _ = synthetic_client(monkeypatch, [answer("A complete Luna answer")])
    request = NewMessageRequest(
        client_request_id="zero-paid-luna",
        role="user",
        model=LUNA,
        tool_choice=[],
        content=[{"type": "text", "value": "Hello"}],
    )
    quote, args, _ = await send_and_execute(
        session, user, conversation, queued, request
    )
    assert "SYSTEM NOTICE:" in args["instructions"]
    assert payloads[0]["instructions"] == provider.shared_instructions(
        LUNA, args["instructions"]
    )
    assert quote["ceiling_units"] == 0 and quote["luna_ceiling"] > 0
    attempts = (await session.exec(select(ProviderAttempt))).all()
    assert len(attempts) == 1 and attempts[0].budget == quote["luna_ceiling"]
    assert attempts[0].included and attempts[0].status == "complete"
    refreshed = await allowance.account(session, user.id)
    assert refreshed.spent == refreshed.granted and refreshed.reserved == 0
    assert (
        0 < refreshed.luna_spent < quote["luna_ceiling"]
        and refreshed.luna_reserved == 0
    )


@pytest.mark.asyncio
async def test_multibyte_image_query_fits_quote_after_real_routing_admission(
    send_case, monkeypatch
):
    session, user, conversation, queued = send_case
    query = "красочный рисунок " * 300
    assert 1000 < len(query.encode("utf-8")) <= MAX_TOOL_QUERY_BYTES
    args = {"query": query, "reference_mode": "none"}
    image_usage = {
        "input_tokens": text_tokens(query),
        "output_tokens": 196,
        "input_tokens_details": {"text_tokens": text_tokens(query), "image_tokens": 0},
    }
    image_response = SimpleNamespace(
        data=[SimpleNamespace(b64_json="synthetic-image")],
        usage=SimpleNamespace(model_dump=lambda: image_usage),
        _request_id="synthetic-image-id",
    )
    payloads, images = synthetic_client(
        monkeypatch,
        [
            {
                "type": "function_call",
                "call_id": "image-call",
                "name": "image_generation",
                "arguments": json.dumps(args, ensure_ascii=False),
            },
            answer("The image is ready."),
        ],
        image_response,
    )
    request = NewMessageRequest(
        client_request_id="multibyte-image",
        role="user",
        model=LUNA,
        tool_choice=["image_generation"],
        required_tool="image_generation",
        image_quality="low",
        content=[{"type": "text", "value": "Нарисуй иллюстрацию"}],
    )
    quote, _, events = await send_and_execute(
        session, user, conversation, queued, request
    )
    attempts = (
        await session.exec(select(ProviderAttempt).order_by(ProviderAttempt.created_at))
    ).all()
    assert [row.model for row in attempts] == [LUNA, FLARE, LUNA]
    assert all(row.status == "complete" for row in attempts)
    assert attempts[1].budget == image_budget("low", len(query.encode("utf-8")))
    assert (
        attempts[1].budget
        <= quote["execution_plan"]["tool_budget_units"]
        <= quote["ceiling_units"]
    )
    images.generate.assert_awaited_once()
    assert images.generate.call_args.kwargs["prompt"] == query
    images.edit.assert_not_awaited()
    assert any(event["type"] == "image.ready" for event in events)
    assert payloads[1]["tool_choice"] == "none"
    row = await allowance.request_row(session, user.id, request.client_request_id)
    assert row.status == "complete" and 0 < row.charged <= quote["ceiling_units"]


@pytest.mark.asyncio
async def test_image_quote_funds_schema_bound_in_four_byte_utf8(
    estimate_case, monkeypatch
):
    monkeypatch.setattr(settings, "SHARED_ALLOWANCE_GENERATION_V2_ENABLED", True)
    session, user, conversation, request = estimate_case
    request.model = LUNA
    maximum = provider.tool_schema("image_generation")["properties"]["query"][
        "maxLength"
    ]
    query = "😀" * maximum
    quote = await allowance_chat.estimate(session, user, conversation, request)
    assert quote["execution_plan"]["tool_budget_units"] >= 2 * image_budget(
        "low", len(query.encode("utf-8"))
    )
