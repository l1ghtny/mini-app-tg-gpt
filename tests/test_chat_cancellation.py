import asyncio
import uuid
from contextlib import aclosing
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.services.chat_cancellation import GenerationStopped, cancellable_events, cancellation_key
from app.api.chat_helpers import handle_cancel_generation
import app.api.chat_helpers as chat
import app.api.helpers as pipeline


class Redis:
    def __init__(self): self.values = {}
    async def get(self, key): return self.values.get(key)
    async def set(self, key, value, **kwargs): self.values[key] = value


@pytest.mark.asyncio
async def test_cancels_idle_provider_and_closes_it():
    redis = Redis()
    closed = asyncio.Event()
    async def source():
        try:
            yield {'type': 'text.delta', 'text': 'partial', 'index': 0}
            await asyncio.Event().wait()
        finally:
            closed.set()
    async with aclosing(cancellable_events(source(), SimpleNamespace(r=redis), 'message')) as events:
        assert (await anext(events))['text'] == 'partial'
        pending = asyncio.create_task(anext(events))
        await redis.set(cancellation_key('message'), '1')
        with pytest.raises(GenerationStopped):
            await asyncio.wait_for(pending, 1)
    assert closed.is_set()


@pytest.mark.asyncio
async def test_stop_before_worker_starts_does_not_call_provider():
    redis = Redis()
    await redis.set(cancellation_key('message'), '1')
    async def source():
        pytest.fail('provider must not be called')
        yield {}
    with pytest.raises(GenerationStopped):
        await anext(cancellable_events(source(), SimpleNamespace(r=redis), 'message'))


@pytest.mark.asyncio
async def test_completed_turn_wins_and_other_message_is_unaffected():
    redis = Redis()
    await redis.set(cancellation_key('older'), '1')
    async def source(): yield {'type': 'done'}
    assert [e async for e in cancellable_events(source(), SimpleNamespace(r=redis), 'newer')] == [{'type': 'done'}]


@pytest.mark.asyncio
async def test_cancel_checks_ownership_and_does_not_cancel_newer_message(monkeypatch):
    cid, mid, newer = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    redis = Redis()
    session = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(conversation_id=cid, role='assistant')))
    owned = AsyncMock(side_effect=HTTPException(404, 'Conversation not found'))
    monkeypatch.setattr(chat, '_load_conversation_for_user', owned)
    args = dict(conversation_id=cid, message_id=mid, session=session, current_user=SimpleNamespace(id=uuid.uuid4()), bus=SimpleNamespace(r=redis))
    with pytest.raises(HTTPException): await handle_cancel_generation(**args)
    assert not redis.values
    owned.side_effect = None
    redis.values[f'conv:{cid}:current'] = str(newer)
    assert await handle_cancel_generation(**args) == {'status': 'finished'}
    assert cancellation_key(str(newer)) not in redis.values
    redis.values[f'conv:{cid}:current'] = str(mid)
    assert await handle_cancel_generation(**args) == {'status': 'requested'}
    assert await handle_cancel_generation(**args) == {'status': 'requested'}
    assert redis.values[cancellation_key(str(mid))] == '1'


@pytest.mark.asyncio
async def test_pipeline_persists_partial_cancellation_and_keeps_consumed_accounting(monkeypatch):
    cid, mid, uid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    redis = Redis()
    conv = SimpleNamespace(last_openai_response_id='resp_old', last_google_interaction_id='old')
    session = SimpleNamespace(get=AsyncMock(return_value=conv),execute=AsyncMock(),commit=AsyncMock(),add=lambda x:None)
    class Context:
        async def __aenter__(self): return session
        async def __aexit__(self,*args): pass
    bus = SimpleNamespace(r=redis, publish=AsyncMock(), mark_done=AsyncMock())
    monkeypatch.setattr(pipeline,'AsyncSession',lambda *a,**k: Context())
    monkeypatch.setattr(pipeline,'_publish_initial_activity',AsyncMock())
    activity = AsyncMock()
    monkeypatch.setattr(pipeline,'_record_and_publish_activity',activity)
    upsert = AsyncMock()
    monkeypatch.setattr(pipeline,'_upsert_text',upsert)
    monkeypatch.setattr(pipeline,'_cleanup_partial_images',AsyncMock())
    monkeypatch.setattr(pipeline,'_clear_active_stream_pointer',AsyncMock())
    monkeypatch.setattr(pipeline,'queue_assistant_index_refresh',AsyncMock())
    async def source(*args,**kwargs):
        yield {'type':'text.delta','index':0,'text':'Keep this partial'}
        await redis.set(cancellation_key(str(mid)),'1')
        await asyncio.Event().wait()
        yield {'type':'done'}
    monkeypatch.setattr(pipeline,'stream_normalized_ai_response',source)
    await asyncio.wait_for(pipeline.generate_and_publish(cid,mid,uid,[],bus,[],request_id='request'),2)
    assert upsert.call_args.args[2] == 'Keep this partial'
    assert conv.last_openai_response_id is None
    assert conv.last_google_interaction_id is None
    statement = session.execute.call_args.args[0]
    from sqlalchemy.dialects import postgresql
    sql = str(statement.compile(dialect=postgresql.dialect(),compile_kwargs={'literal_binds':True}))
    assert 'reserved' in sql and 'refunded' in sql
    assert any(call.kwargs['event']['type']=='cancelled' for call in activity.call_args_list)
    bus.publish.assert_any_await(str(mid),{'type':'done','cancelled':'true'})
    bus.mark_done.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_closes_openai_http_stream(monkeypatch):
    import app.services.openai_service as provider
    import app.services.ai_service as routing
    redis = Redis()
    started = asyncio.Event()
    closed = asyncio.Event()
    class HttpStream:
        def __aiter__(self): return self
        async def __anext__(self):
            started.set()
            await asyncio.Event().wait()
        async def close(self): closed.set()
    monkeypatch.setattr(provider, 'client', SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(return_value=HttpStream()))))
    async def consume():
        async with aclosing(cancellable_events(routing.stream_normalized_ai_response([],model='gpt-5.4-nano'),SimpleNamespace(r=redis),'provider')) as events:
            async for _ in events: pass
    task=asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(),2)
    await redis.set(cancellation_key('provider'),'1')
    with pytest.raises(GenerationStopped): await asyncio.wait_for(task,2)
    assert closed.is_set()


@pytest.mark.asyncio
async def test_terminal_event_runs_provider_bookkeeping_despite_late_stop():
    redis = Redis()
    bookkeeping = []
    async def source():
        yield {'type': 'done'}
        bookkeeping.append('usage logged')
    async with aclosing(cancellable_events(source(), SimpleNamespace(r=redis), 'message')) as events:
        assert await anext(events) == {'type': 'done'}
        await redis.set(cancellation_key('message'), '1')
        with pytest.raises(StopAsyncIteration): await anext(events)
    assert bookkeeping == ['usage logged']
