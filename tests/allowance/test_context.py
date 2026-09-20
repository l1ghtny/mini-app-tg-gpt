from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.services import allowance_context as context


def message(text, identifier=None, role="user"):
    return {
        "role": role,
        "content": [{"type": "input_text", "text": text}],
        "_message_id": identifier,
    }


def test_partition_preserves_recent_turns_and_internal_ids_never_reach_provider():
    old, recent = context.context_partition(
        [message("a" * 100, "old"), message("new", "new")], 20
    )
    assert len(old) == 1 and recent[0]["content"][0]["text"] == "new"
    assert "_message_id" not in context.clean_messages(recent)[0]


@pytest.mark.asyncio
async def test_existing_summary_reused_without_another_provider_call(monkeypatch):
    monkeypatch.setattr(context.settings, "SHARED_ALLOWANCE_HISTORY_TOKENS", 5)
    conv = SimpleNamespace(
        user_id="owner",
        history_summary="Keep the budget at 42.",
        history_summary_up_to_message_id="old",
        updated_at=1,
    )
    session = AsyncMock()
    session.get.return_value = conv
    session.__aenter__.return_value = session
    monkeypatch.setattr(context, "AsyncSession", lambda *a, **kw: session)
    run = SimpleNamespace(conversation_id="chat", user_id="owner", response=AsyncMock())
    result = await context.compress_context(
        run, [message("a" * 100, "old"), message("new", "new")]
    )
    run.response.assert_not_called()
    assert "42" in result[0]["content"][0]["text"]
    assert result[-1]["content"][0]["text"] == "new"


@pytest.mark.asyncio
async def test_deleted_summary_boundary_is_not_reused(monkeypatch):
    monkeypatch.setattr(context.settings, "SHARED_ALLOWANCE_HISTORY_TOKENS", 5)
    conv = SimpleNamespace(
        user_id="owner",
        history_summary="Obsolete secret constraint",
        history_summary_up_to_message_id="deleted",
        updated_at=1,
    )
    session = AsyncMock()
    session.get.return_value = conv
    session.__aenter__.return_value = session
    monkeypatch.setattr(context, "AsyncSession", lambda *a, **kw: session)
    run = SimpleNamespace(
        conversation_id="chat",
        user_id="owner",
        response=AsyncMock(
            return_value=(SimpleNamespace(output_text="Updated summary"), [])
        ),
    )
    result = await context.compress_context(
        run, [message("Corrected old history" * 10), message("new", "new")]
    )
    assert "Obsolete" not in str(run.response.call_args)
    assert "Updated summary" in str(result)
