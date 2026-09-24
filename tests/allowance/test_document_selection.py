from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from app.services import shared_chat_provider as provider


@pytest.mark.asyncio
@pytest.mark.parametrize('choice,expected', [([], ['file_search']), (['web_search'], ['web_search', 'file_search']), ('none', ['file_search']), ('auto', 'auto'), (['auto'], ['auto'])])
async def test_attaching_enables_search_atomically(document_helpers, monkeypatch, choice, expected):
    from app.db.models import Conversation, AppUser
    from app.schemas.documents import DocumentCapabilitiesResponse
    user = AppUser(id=uuid.uuid4())
    conversation = Conversation(id=uuid.uuid4(), user_id=user.id, tool_choice=choice)
    doc = SimpleNamespace(id=uuid.uuid4())
    session = SimpleNamespace(get=AsyncMock(return_value=conversation),
        exec=AsyncMock(side_effect=[SimpleNamespace(all=lambda: [doc]), SimpleNamespace(all=lambda: [])]),
        flush=AsyncMock(), commit=AsyncMock(), add=lambda obj: None)
    monkeypatch.setattr(document_helpers, '_document_has_attachable_state', lambda *args: True)
    monkeypatch.setattr(document_helpers, '_refresh_expiration', lambda *args: None)
    monkeypatch.setattr(document_helpers, 'get_document_capabilities', AsyncMock(return_value=DocumentCapabilitiesResponse(
        status='active', max_active_docs=50, active_doc_count=1, max_pinned_docs=25, pinned_doc_count=0,
        max_storage_bytes=1000, used_storage_bytes=10, remaining_storage_bytes=990, max_file_size_bytes=100, doc_retention_hours=120)))
    response = await document_helpers.replace_conversation_documents(session=session, user=user,
        conversation_id=conversation.id, document_ids=[doc.id, doc.id])
    assert response.document_ids == [doc.id]
    assert response.tool_choice == expected == conversation.tool_choice
    session.commit.assert_awaited_once()


@pytest.fixture
def document_helpers(monkeypatch):
    from app.api import document_helpers
    monkeypatch.setattr(document_helpers, '_resolve_document_provider', lambda **kw: ('openai', 'openai', False))
    return document_helpers


def test_new_conversations_and_messages_default_to_auto():
    from app.db.models import Conversation
    from app.schemas.chat import NewMessageRequest
    assert Conversation(user_id=uuid.uuid4()).tool_choice == 'auto'
    request = NewMessageRequest(client_request_id='default-tool-check', model='gpt-6-astra', role='user', content=[{'type': 'text', 'value': 'Hello'}])
    assert request.tool_choice == 'auto'


@pytest.mark.parametrize('choice,enabled', [('auto', True), (['auto'], True), ([], False)])
def test_auto_allows_attached_search_but_explicit_none_still_disables_it(choice, enabled):
    from app.api.chat_helpers import _resolve_openai_tooling
    available = [{'type': 'file_search', 'vector_store_ids': ['vs-doc']}]
    tools, resolved, _ = _resolve_openai_tooling(choice, available)
    assert tools == (available if enabled else [])
    assert resolved == ('auto' if enabled else 'none')


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ['gpt-5.6-luna', 'claude-fable-5-1'])
@pytest.mark.parametrize('mode,expected', [('auto', None), ('required', 'file_search')])
async def test_document_permissions_are_not_a_tool_name(monkeypatch, model, mode, expected):
    monkeypatch.setattr(provider, 'compress_context', AsyncMock(return_value=[]))
    seen = []
    async def turn(run, history, model, system, tools, required, effort, index):
        seen.append((tools, required))
        yield {'type': 'turn.result', 'output': [], 'calls': []}
    monkeypatch.setattr(provider, 'openai_turn', turn)
    monkeypatch.setattr(provider, 'claude_turn', turn)
    stream = provider.stream_shared_response([], model,
        tools=[{'type': 'file_search', 'vector_store_ids': ['vs-first', 'vs-second']}, {'type': 'web_search'}],
        tool_choice={'type': 'allowed_tools', 'mode': mode, 'tools': [{'type': 'file_search'}]})
    if expected:
        with pytest.raises(RuntimeError, match='did not use'):
            _ = [e async for e in stream]
    else:
        assert (await anext(stream))['type'] == 'text.done'
        await stream.aclose()
    assert seen == [({'file_search': {'type': 'file_search', 'vector_store_ids': ['vs-first', 'vs-second']}}, expected)]


@pytest.mark.asyncio
async def test_search_reads_both_attached_stores(monkeypatch):
    search = AsyncMock(side_effect=[SimpleNamespace(data=[SimpleNamespace(filename=name, content=[SimpleNamespace(type='text', text=value)])])
        for name, value in [('first.txt', 'SAPPHIRE-731'), ('second.txt', 'COPPER-482')]])
    monkeypatch.setattr(provider, 'client', SimpleNamespace(with_options=lambda **kw: SimpleNamespace(vector_stores=SimpleNamespace(search=search))))
    run = SimpleNamespace(start=AsyncMock(return_value='attempt'), finish=AsyncMock())
    events = [e async for e in provider.run_tool(run, 'file_search', {'query': 'codes'}, {'file_search': {'vector_store_ids': ['vs-first', 'vs-second']}}, [], 0)]
    result = next(e['result'] for e in events if e['type'] == 'tool.result')
    assert 'SAPPHIRE-731' in result and 'COPPER-482' in result
    assert [c.kwargs['vector_store_id'] for c in search.call_args_list] == ['vs-first', 'vs-second']
    assert run.finish.call_args.kwargs['units'] == 5000
