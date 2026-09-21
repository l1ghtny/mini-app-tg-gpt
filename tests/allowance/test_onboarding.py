import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession
from app.db.onboarding import OnboardingVisit
from app.services.onboarding import progress, claim_nudge
from app.services import allowance as a


@pytest.mark.asyncio
async def test_visits_dedupe_and_count_only_successful_tasks(db):
    engine, session, user = db
    async with engine.begin() as conn:
        await conn.run_sync(OnboardingVisit.__table__.create)
    first = uuid4()
    assert (await progress(session, user.id, first))["sessions"] == 1
    assert (await progress(session, user.id, uuid4()))["sessions"] == 1
    visit = await session.get(OnboardingVisit, (user.id, first))
    visit.created_at = datetime.now(UTC).replace(tzinfo=None)-timedelta(days=1)
    session.add(visit); await session.commit()
    second = uuid4()
    state = await progress(session, user.id, second)
    assert state == dict(sessions=2, active_days=2, successful_answers=0, successful_chats=0)
    chat_id = uuid4()
    for key, success in [('first', True), ('followup', True), ('failed', False)]:
        await a.reserve(session, user_id=user.id, request_id=key, conversation_id=chat_id, model='gpt-5.6-terra', ceiling=100)
        await a.settle(session, user.id, key, success=success)
    state = await progress(session, user.id, second)
    assert state["successful_answers"] == 2 and state["successful_chats"] == 1 and state["sessions"] == 2


@pytest.mark.asyncio
async def test_concurrent_surfaces_claim_only_one_suggestion_and_dismissal_persists(db):
    engine, session, user = db
    async def claim(item):
        async with AsyncSession(engine, expire_on_commit=False) as other:
            return await claim_nudge(other, user.id, item)
    claims = await asyncio.gather(claim('personalisation_v2'), claim('folders_v2'))
    assert sum(claims) == 1
    assert not await claim('install_v2')
    await session.refresh(user)
    user.onboarding_state = {'personalisation_v2': {'dismissed_at': '2020-01-01T00:00:00+00:00'}}
    session.add(user); await session.commit()
    assert not await claim('personalisation_v2')
    assert await claim('folders_v2')


@pytest.mark.asyncio
async def test_authenticated_onboarding_contract_and_default_model(db, monkeypatch):
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport
    from app.api.user_settings import user_settings
    from app.api.dependencies import get_current_user
    from app.db.database import get_session
    engine, session, user = db
    monkeypatch.setattr(a.settings, 'SHARED_ALLOWANCE_TRIAL_ENABLED', True)
    async with engine.begin() as conn:
        await conn.run_sync(OnboardingVisit.__table__.create)
    app = FastAPI()
    app.include_router(user_settings)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_session] = lambda: session
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/user/settings')
        assert response.status_code == 200
        assert response.json()['default_text_model'] == 'claude-sonnet-5'
        response = await client.post('/user/settings/onboarding/visit', json={'session_id': str(uuid4())})
        assert response.status_code == 200 and response.json()['successful_answers'] == 0
        response = await client.put('/user/settings', json={'onboarding_events': [{'item': 'personalisation_v2', 'action': 'dismissed'}]})
        assert response.status_code == 200 and response.json()['onboarding_state']['personalisation_v2']['dismissed_at']
        response = await client.post('/user/settings/onboarding/claim', json={'item': 'personalisation_v2'})
        assert response.json() == {'claimed': False}
