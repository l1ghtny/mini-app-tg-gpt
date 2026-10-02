import copy
import json
from types import SimpleNamespace

import pytest

from app.services.allowance_policy import input_upper_bound
from app.services.generation_budget import execution_plan
from app.services.shared_chat_loop import (
    CONTEXT_STOP,
    bounded_result,
    result_tokens,
    results_fit,
    tool_result_messages,
)


@pytest.mark.parametrize("value", [
    'quote " and slash \\\n' * 3000,
    "\x00\n\t" * 3000,
    "😀" * 4000,
    json.dumps({"text": 'quote " and slash \\\n' * 3000, "sources": [
        {"url": "https://example.test/complete-source", "title": "Evidence"},
    ]}),
])
def test_fixed_result_budget_counts_escaped_provider_input(value):
    bounded = bounded_result(value)
    assert result_tokens(bounded) <= 2048
    if value.startswith('{"text"'):
        assert json.loads(bounded)["sources"][0]["url"] == "https://example.test/complete-source"


@pytest.mark.parametrize("model,claude", [
    ("gpt-5.6-terra", False), ("claude-sonnet-5", True),
])
def test_all_pending_results_count_without_rewriting_provider_history(model, claude):
    calls = [dict(id="call-" + str(i), name="file_search", args={"query": str(i)}) for i in range(6)]
    if claude:
        history = [{"role": "assistant", "content": [
            {"type": "thinking", "thinking": "Signed provider block", "signature": "keep-exact"},
            *[{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]} for c in calls],
        ]}]
    else:
        history = [{"type": "function_call", "call_id": c["id"], "name": c["name"], "arguments": json.dumps(c["args"])} for c in calls]
    original = copy.deepcopy(history)
    values = {c["id"]: " evidence" * 2048 if i < 2 else CONTEXT_STOP for i, c in enumerate(calls)}
    matched = tool_result_messages(calls, values, claude)
    plan = execution_plan(model, [])
    limit = input_upper_bound(history + matched, "Stable system", model=model) + plan.max_output_tokens
    run = SimpleNamespace(plan=plan, execution={"risk_policy": {"context_tokens": limit}})
    assert results_fit(run, history, calls, values, model, "Stable system", [])
    assert not results_fit(run, history, calls, {c["id"]: " evidence" * 2048 for c in calls}, model, "Stable system", [])
    assert history == original
    ids = [p["tool_use_id"] for p in matched[0]["content"]] if claude else [m["call_id"] for m in matched]
    assert ids == [c["id"] for c in calls]
