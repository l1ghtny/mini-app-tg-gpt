import json
import asyncio
from datetime import timedelta
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
from app.services import shared_chat_provider as provider, allowance_tasks
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
    from app.services import shared_chat_loop

    monkeypatch.setattr(shared_chat_loop, "engine", engine)
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
            self.status = "completed"
            if isinstance(output, tuple):
                self.output, self.status = output

        def __aiter__(self):
            async def events():
                yield SimpleNamespace(
                    type="response." + self.status,
                    response=SimpleNamespace(
                        id=f"synthetic-{len(payloads)}",
                        status=self.status,
                        incomplete_details=SimpleNamespace(reason="max_output_tokens")
                        if self.status == "incomplete"
                        else None,
                        usage=self.usage,
                        output=[
                            SimpleNamespace(model_dump=lambda x=x, **kw: x)
                            for x in (
                                self.output
                                if isinstance(self.output, list)
                                else [self.output]
                            )
                        ],
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


def tool_call(name, query, key):
    return {
        "type": "function_call",
        "call_id": key,
        "name": name,
        "arguments": json.dumps({"query": query}),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["web_search", "file_search"])
async def test_iterative_refined_retrieval_then_cited_answer(
    send_case, monkeypatch, name
):
    session, user, conversation, queued = send_case
    payloads, _ = synthetic_client(
        monkeypatch,
        [
            tool_call(name, "initial", "one"),
            tool_call(name, "refined", "two"),
            answer("Evidence from report.pdf and https://example.test/source"),
        ],
    )
    if name == "file_search":
        from app.api import document_helpers

        ready = AsyncMock(return_value=["store"])
        monkeypatch.setattr(chat, "list_conversation_ready_vector_store_ids", ready)
        monkeypatch.setattr(
            document_helpers, "list_conversation_ready_vector_store_ids", ready
        )
        search = AsyncMock(
            side_effect=lambda **kw: SimpleNamespace(
                data=[
                    SimpleNamespace(
                        filename="report.pdf",
                        content=[
                            SimpleNamespace(type="text", text=kw["query"] + " evidence")
                        ],
                    )
                ]
            )
        )
        provider.client.vector_stores = SimpleNamespace(search=search)
    else:
        main_create = provider.client.responses.create
        queries = []

        async def create(**kw):
            if kw.get("stream"):
                return await main_create(**kw)
            queries.append(kw["input"][0]["content"][0]["text"])
            output = {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": queries[-1] + " evidence",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url": "https://example.test/source",
                                "title": "Source",
                            }
                        ],
                    }
                ],
            }
            return SimpleNamespace(
                status="completed",
                id="search-" + str(len(queries)),
                usage={"input_tokens": 100, "output_tokens": 100},
                output_text=queries[-1] + " evidence",
                output=[SimpleNamespace(model_dump=lambda **kw: output)],
            )

        provider.client.responses.create = create
    request = NewMessageRequest(
        client_request_id="iterative-" + name,
        role="user",
        model="gpt-5.6-terra",
        tool_choice=[name],
        required_tool=name,
        content=[{"type": "text", "value": "Research this question"}],
    )
    quote, _, _ = await send_and_execute(session, user, conversation, queued, request)
    assert not quote["needs_confirmation"]
    assert len(payloads) == 3
    assert payloads[1]["max_output_tokens"] == quote["max_output_tokens"]
    assert payloads[1]["tool_choice"] == "auto"
    outputs = [
        m["output"]
        for m in payloads[2]["input"]
        if m.get("type") == "function_call_output"
    ]
    assert len(outputs) == 2 and "refined" in outputs[-1]
    assert (
        "https://example.test/source" if name == "web_search" else "report.pdf"
    ) in outputs[-1]
    row = await allowance.request_row(session, user.id, request.client_request_id)
    assert row.execution_state["tools"] == 2 and row.status == "complete"
    if name == "file_search":
        assert search.await_count == 2
    else:
        assert queries == ["initial", "refined"]


@pytest.mark.asyncio
async def test_parallel_duplicate_and_empty_tools_end_with_full_final(
    send_case, monkeypatch
):
    session, user, conversation, queued = send_case
    from app.api import document_helpers

    ready = AsyncMock(return_value=["store"])
    monkeypatch.setattr(chat, "list_conversation_ready_vector_store_ids", ready)
    monkeypatch.setattr(
        document_helpers, "list_conversation_ready_vector_store_ids", ready
    )
    payloads, _ = synthetic_client(
        monkeypatch,
        [
            [
                tool_call("file_search", "one", "1"),
                tool_call("file_search", "two", "2"),
                tool_call("file_search", "one", "3"),
                tool_call("file_search", "three", "4"),
            ],
            answer(
                "The excerpts did not resolve this question; report.pdf remains an evidence gap."
            ),
        ],
    )
    active, peak = 0, 0

    async def search(**kw):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return SimpleNamespace(data=[])

    search_mock = AsyncMock(side_effect=search)
    provider.client.vector_stores = SimpleNamespace(search=search_mock)
    req = NewMessageRequest(
        client_request_id="parallel",
        role="user",
        model="gpt-5.6-terra",
        tool_choice=["file_search"],
        content=[{"type": "text", "value": "Compare the files"}],
    )
    quote, _, _ = await send_and_execute(session, user, conversation, queued, req)
    assert peak == 2 and search_mock.await_count == 2
    row = await allowance.request_row(session, user.id, req.client_request_id)
    assert row.execution_state["tools"] == 2
    assert payloads[-1]["max_output_tokens"] == quote["max_output_tokens"]
    assert payloads[-1]["tool_choice"] == "none"
    matched = [
        m for m in payloads[-1]["input"] if m.get("type") == "function_call_output"
    ]
    assert len(matched) == 4
    assert matched[0]["output"] == matched[2]["output"] == "[]"
    assert "not executed" in matched[-1]["output"]


@pytest.mark.asyncio
async def test_research_deadline_preserves_answer_capacity(send_case, monkeypatch):
    session, user, conversation, queued = send_case
    payloads, _ = synthetic_client(
        monkeypatch,
        [
            tool_call("web_search", "first", "one"),
            answer("Use https://example.test/source. Further evidence is unavailable."),
        ],
    )
    original = allowance_tasks.now
    clock_offset = timedelta()
    monkeypatch.setattr(allowance_tasks, "now", lambda: original() + clock_offset)
    main_create = provider.client.responses.create

    async def create(**kw):
        nonlocal clock_offset
        if kw.get("stream"):
            return await main_create(**kw)
        clock_offset = timedelta(seconds=121)
        output = answer("Evidence https://example.test/source")
        return SimpleNamespace(
            status="completed",
            id="search",
            usage={"input_tokens": 100, "output_tokens": 100},
            output_text="Evidence https://example.test/source",
            output=[SimpleNamespace(model_dump=lambda **kw: output)],
        )

    provider.client.responses.create = create
    req = NewMessageRequest(
        client_request_id="deadline",
        role="user",
        model="gpt-5.6-terra",
        tool_choice=["web_search"],
        content=[{"type": "text", "value": "Research"}],
    )
    quote, _, _ = await send_and_execute(session, user, conversation, queued, req)
    assert len(payloads) == 2 and payloads[-1]["tool_choice"] == "none"
    assert payloads[-1]["max_output_tokens"] == quote["max_output_tokens"]


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
    assert payloads[0]["instructions"] == provider.iterative_instructions(
        provider.shared_instructions(LUNA, args["instructions"])
    )
    assert quote["ceiling_units"] == 0 and quote["luna_ceiling"] > 0
    attempts = (await session.exec(select(ProviderAttempt))).all()
    assert (
        len(attempts) == 1
        and attempts[0].budget < quote["execution_plan"]["supplier_ceiling"]
    )
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
    assert attempts[1].budget <= quote["execution_plan"]["supplier_ceiling"]
    images.generate.assert_awaited_once()
    assert images.generate.call_args.kwargs["prompt"] == query
    images.edit.assert_not_awaited()
    assert any(event["type"] == "image.ready" for event in events)
    assert payloads[1]["max_output_tokens"] == quote["max_output_tokens"]
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
    assert quote["execution_plan"]["supplier_ceiling"] >= image_budget(
        "low", len(query.encode("utf-8"))
    )


@pytest.mark.asyncio
async def test_known_cap_recovery_keeps_document_evidence_without_replaying_tool(
    send_case, monkeypatch
):
    session, user, conversation, queued = send_case
    from app.api import document_helpers

    ready = AsyncMock(return_value=["store"])
    monkeypatch.setattr(chat, "list_conversation_ready_vector_store_ids", ready)
    monkeypatch.setattr(
        document_helpers, "list_conversation_ready_vector_store_ids", ready
    )
    payloads, _ = synthetic_client(
        monkeypatch,
        [
            tool_call("file_search", "facts", "one"),
            (answer("Partial answer from report.pdf"), "incomplete"),
            answer("Continuation citing report.pdf"),
        ],
    )
    search = AsyncMock(
        return_value=SimpleNamespace(
            data=[
                SimpleNamespace(
                    filename="report.pdf",
                    content=[
                        SimpleNamespace(
                            type="text", text="Verified fact from the document"
                        )
                    ],
                )
            ]
        )
    )
    provider.client.vector_stores = SimpleNamespace(search=search)
    req = NewMessageRequest(
        client_request_id="evidence-recovery",
        role="user",
        model="gpt-5.6-terra",
        tool_choice=["file_search"],
        content=[{"type": "text", "value": "Analyse the document"}],
    )
    _, _, _ = await send_and_execute(session, user, conversation, queued, req)
    search.assert_awaited_once()
    assert payloads[-1]["tool_choice"] == "none"
    assert "Verified fact from the document" in json.dumps(payloads[-1]["input"])
    assert "Partial answer from report.pdf" in json.dumps(payloads[-1]["input"])
    attempts = (
        await session.exec(select(ProviderAttempt).order_by(ProviderAttempt.created_at))
    ).all()
    assert len(attempts) == 4 and sum(p.recovery for p in attempts) == 1
    assert attempts[-2].status == "failed" and attempts[-2].customer_units == 0
    row = await allowance.request_row(session, user.id, req.client_request_id)
    assert row.charged == sum(p.customer_units for p in attempts)


@pytest.mark.asyncio
async def test_duplicate_planning_turns_are_bounded_without_extra_retrieval(
    send_case, monkeypatch
):
    session, user, conversation, queued = send_case
    from app.api import document_helpers

    ready = AsyncMock(return_value=["store"])
    monkeypatch.setattr(chat, "list_conversation_ready_vector_store_ids", ready)
    monkeypatch.setattr(
        document_helpers, "list_conversation_ready_vector_store_ids", ready
    )
    payloads, _ = synthetic_client(
        monkeypatch,
        [tool_call("file_search", "same", str(i)) for i in range(6)]
        + [answer("Answer from report.pdf; further questions remain.")],
    )
    search = AsyncMock(
        return_value=SimpleNamespace(
            data=[
                SimpleNamespace(
                    filename="report.pdf",
                    content=[SimpleNamespace(type="text", text="Fact")],
                )
            ]
        )
    )
    provider.client.vector_stores = SimpleNamespace(search=search)
    req = NewMessageRequest(
        client_request_id="duplicate-turns",
        role="user",
        model="gpt-5.6-terra",
        tool_choice=["file_search"],
        content=[{"type": "text", "value": "Research"}],
    )
    quote, _, _ = await send_and_execute(session, user, conversation, queued, req)
    search.assert_awaited_once()
    row = await allowance.request_row(session, user.id, req.client_request_id)
    assert (
        row.execution_state["planning_turns"] == 6 and row.execution_state["tools"] == 1
    )
    assert len(payloads) == 7 and payloads[-1]["tool_choice"] == "none"
    assert payloads[-1]["max_output_tokens"] == quote["max_output_tokens"]
