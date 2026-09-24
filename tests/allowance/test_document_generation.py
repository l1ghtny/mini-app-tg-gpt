import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import allowance_chat, shared_chat_provider as provider
from app.services.allowance_policy import DOCUMENT_SEARCH_TOKENS, text_tokens


def test_large_results_are_bounded_and_keep_both_documents():
    groups = [
        [
            {"filename": name, "text": f"{name} fact {n} " + "evidence " * 5000}
            for n in range(4)
        ]
        for name in ("first.txt", "second.txt")
    ]
    payload = provider.bounded_document_results(groups)
    assert text_tokens(payload) <= DOCUMENT_SEARCH_TOKENS
    passages = json.loads(payload)
    assert {p["filename"] for p in passages} == {"first.txt", "second.txt"}
    assert all(p["text"] for p in passages)
    assert len(passages) == 8


@pytest.mark.asyncio
@pytest.mark.parametrize("model,batch", [("gpt-6-astra", 1), ("claude-fable-5-1", 3)])
async def test_document_search_finishes_without_retrieval_loop_or_batch_abort(
    monkeypatch, model, batch
):
    monkeypatch.setattr(provider, "compress_context", AsyncMock(return_value=[]))
    turns = []

    async def turn(run, history, model, system, tools, required, effort, index):
        turns.append((copy.deepcopy(history), tools))
        if len(turns) == 1:
            calls = [
                {"id": str(i), "name": "file_search", "args": {"query": str(i)}}
                for i in range(batch)
            ]
            yield {"type": "turn.result", "output": [], "calls": calls}
        else:
            yield {
                "type": "text.delta",
                "text": "Answer from both documents.",
                "index": index,
            }
            yield {"type": "turn.result", "output": [], "calls": []}

    executed = []

    async def run_tool(run, name, args, *rest):
        executed.append(args)
        yield {"type": "tool.result", "result": "first.txt: A; second.txt: B"}

    monkeypatch.setattr(provider, "openai_turn", turn)
    monkeypatch.setattr(provider, "claude_turn", turn)
    monkeypatch.setattr(provider, "run_tool", run_tool)
    events = [
        e
        async for e in provider.stream_shared_response(
            [],
            model,
            tools=[{"type": "file_search", "vector_store_ids": ["one", "two"]}],
        )
    ]
    assert events[-1]["type"] == "done"
    assert len(executed) == min(batch, 2)
    assert turns[1][1] == {}
    if batch == 3:
        results = turns[1][0][-1]["content"]
        assert len(results) == 3
        assert "not executed" in results[-1]["content"]
        assert [r["tool_use_id"] for r in results] == ["0", "1", "2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", ["auto", ["auto"], ["file_search"]])
async def test_quote_accounts_for_document_followup_and_binds_selection(
    estimate_case, monkeypatch, choice
):
    from app.api import document_helpers

    s, u, c, r = estimate_case
    r.tool_choice = choice
    r.required_tool = None
    account = await allowance_chat.allowance.account(s, u.id)
    account.plan = "premium"
    account.granted = 6250000
    r.model = "gpt-6-astra"
    stores = AsyncMock(return_value=[])
    monkeypatch.setattr(
        document_helpers, "list_conversation_ready_vector_store_ids", stores
    )
    empty = await allowance_chat.estimate(s, u, c, r)
    stores.return_value = ["one", "two"]
    docs = await allowance_chat.estimate(s, u, c, r)
    assert docs["ceiling_units"] > empty["ceiling_units"]
    assert docs["estimated_max_percent"] > empty["estimated_max_percent"]
    assert docs["estimate_reference"] != empty["estimate_reference"]
    r.spend_limit_units = 20000
    limited = await allowance_chat.estimate(s, u, c, r)
    assert limited["ceiling_units"] == 20000


@pytest.mark.asyncio
async def test_stream_records_real_incomplete_reason(monkeypatch):
    response = SimpleNamespace(
        id="response-test",
        status="incomplete",
        output=[],
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        usage={"input_tokens": 100, "output_tokens": 300},
    )

    class Stream:
        def __aiter__(self):
            return self.events()

        async def events(self):
            yield SimpleNamespace(type="response.incomplete", response=response)

        close = AsyncMock()

    stream = Stream()
    create = AsyncMock(return_value=stream)
    monkeypatch.setattr(
        provider,
        "client",
        SimpleNamespace(
            with_options=lambda **kw: SimpleNamespace(
                responses=SimpleNamespace(create=create)
            )
        ),
    )
    run = SimpleNamespace(
        text_capacity=AsyncMock(return_value=300),
        start=AsyncMock(return_value="attempt"),
        finish=AsyncMock(),
    )
    with pytest.raises(provider.ProviderResponseError) as error:
        _ = [
            e
            async for e in provider.openai_turn(
                run, [], "gpt-6-astra", "", {}, None, "medium", 0
            )
        ]
    assert error.value.reason == "max_output_tokens"
    usage = run.finish.call_args.args[2]
    assert usage["response_status"] == "incomplete"
    assert usage["incomplete_reason"] == "max_output_tokens"
    assert run.finish.call_args.kwargs["success"] is False
    stream.close.assert_awaited_once()


@pytest.mark.parametrize("model", ["gpt-6-astra", "claude-fable-5-1"])
def test_incident_sized_budget_keeps_room_for_final_answer(model):
    from app.services.allowance_policy import affordable_output, step_budget

    original = [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "question " * 1000}],
        }
    ]
    evidence = provider.bounded_document_results(
        [
            [{"filename": name, "text": "retrieved fact " * 10000}]
            for name in ["first.txt", "second.txt"]
        ]
    )
    history = [
        *original,
        {"role": "user", "content": [{"type": "input_text", "text": evidence}]},
    ]
    # Observed Astra request ceiling, minus first routing and two-store search.
    remaining = 269963 - 14420 - 5000
    maximum = affordable_output(
        model, history, provider.shared_instructions(model, ""), remaining, target=4096
    )
    assert maximum >= 2048
    assert (
        step_budget(
            model, history, provider.shared_instructions(model, ""), max_output=maximum
        )
        <= remaining
    )
