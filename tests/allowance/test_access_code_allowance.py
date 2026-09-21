from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
from sqlmodel import select

from app.api.access_codes import access_codes
from app.api.dependencies import get_current_user
from app.db.database import get_session
from app.db.subscription_tiers import AccessCode, AccessCodeDiscount, SubscriptionTier, UserSubscription, UserTierDiscount, UsagePack
from app.services import allowance as a


async def prepare(db, monkeypatch, tier_name=None, discount=False):
    engine, session, user = db
    monkeypatch.setattr(a.settings, 'SHARED_ALLOWANCE_TRIAL_ENABLED', True)
    monkeypatch.setattr(a.settings, 'SHARED_ALLOWANCE_PRIVATE_USER_IDS', set())
    monkeypatch.setattr(a.settings, 'DEPLOYMENT_CHANNEL', 'production')
    async with engine.begin() as conn:
        for model in (UsagePack, AccessCode, AccessCodeDiscount, UserTierDiscount):
            await conn.run_sync(lambda c, m=model: m.__table__.create(c))
    tier = SubscriptionTier(name=tier_name or 'Premium', is_public=not bool(tier_name))
    session.add(tier); await session.flush()
    code = AccessCode(code='TEST-INVITATION', tier_id=None if discount else tier.id, max_uses=10)
    session.add(code); await session.flush()
    if discount:
        session.add(AccessCodeDiscount(access_code_id=code.id, tier_id=tier.id, discount_percent=20, duration_months=1))
    await session.commit()
    app = FastAPI(); app.include_router(access_codes)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_session] = lambda: session
    return app, session, user, tier, code


@pytest.mark.asyncio
async def test_redeeming_private_code_exits_trial_without_rollout_membership(db, monkeypatch):
    app, s, user, tier, code = await prepare(db, monkeypatch, 'Close Friends Tier')
    original = await a.account(s, user.id); original.spent = 100
    s.add(original); await s.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post(f'/access_codes/{code.id}/redeem')
    assert response.status_code == 202
    state = await a.snapshot(s, user.id)
    assert state['plan'] == 'premium' and state['tier_name'] == 'Close Friends Tier'
    assert state['granted_units'] == 6_250_000 and state['trial'] is None
    sub = (await s.exec(select(UserSubscription).where(UserSubscription.user_id == user.id))).one()
    assert sub.auto_renew_enabled is False
    await s.refresh(original)
    assert original.spent == 100 and original.plan == 'starter'
    monkeypatch.setattr(a.settings, 'DEPLOYMENT_CHANNEL', 'beta')
    monkeypatch.setattr(a.settings, 'BETA_ALLOWED_USER_IDS', set())
    assert not a.enabled(user.id)


@pytest.mark.asyncio
async def test_expired_private_membership_can_be_renewed_by_a_new_code(db, monkeypatch):
    app, s, user, tier, code = await prepare(db, monkeypatch, 'Smooth tier')
    sub = UserSubscription(user_id=user.id, tier_id=tier.id, expires_at=datetime.now()-timedelta(days=1))
    s.add(sub); await s.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post(f'/access_codes/{code.id}/redeem')
    assert response.status_code == 202
    await s.refresh(sub)
    assert sub.expires_at > datetime.now()
    assert sub.auto_renew_enabled is False
    assert (await a.snapshot(s, user.id))['plan'] == 'premium'


@pytest.mark.asyncio
async def test_discount_is_saved_without_granting_private_access(db, monkeypatch):
    app, s, user, _, code = await prepare(db, monkeypatch, discount=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post(f'/access_codes/{code.id}/redeem')
    assert response.status_code == 202
    discounts = (await s.exec(select(UserTierDiscount).where(UserTierDiscount.user_id == user.id))).all()
    assert len(discounts) == 1 and discounts[0].discount_percent == 20
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.post(f'/access_codes/{code.id}/redeem')).status_code == 202
    assert len((await s.exec(select(UserTierDiscount).where(UserTierDiscount.user_id == user.id))).all()) == 1
    await s.refresh(code)
    assert code.used_count == 1
    assert (await a.snapshot(s, user.id))['plan'] == 'starter'


@pytest.mark.asyncio
async def test_unsupported_old_plan_is_not_consumed(db, monkeypatch):
    app, s, _, _, code = await prepare(db, monkeypatch, 'Old paid plan')
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post(f'/access_codes/{code.id}/redeem')
    assert response.status_code == 409
    await s.refresh(code)
    assert code.used_count == 0
