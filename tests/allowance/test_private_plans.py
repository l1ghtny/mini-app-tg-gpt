from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlmodel import select

from app.db.allowance import AllowanceAccount, AllowanceEvent
from app.db.subscription_tiers import SubscriptionTier, UserSubscription
from app.services import allowance as a
from app.services.allowance_policy import BASE_GRANT, PLANS


async def enroll(db, monkeypatch, names):
    engine, session, user = db
    async with engine.begin() as conn:
        for table in (SubscriptionTier.__table__, UserSubscription.__table__):
            await conn.run_sync(lambda connection, t=table: t.create(connection, checkfirst=True))
    subscriptions = []
    for name in names:
        tier = SubscriptionTier(name=name, is_public=False)
        session.add(tier)
        await session.flush()
        sub = UserSubscription(user_id=user.id, tier_id=tier.id, started_at=datetime(2026, 1, 1))
        session.add(sub)
        subscriptions.append(sub)
    await session.commit()
    monkeypatch.setattr(a.settings, "SHARED_ALLOWANCE_PRIVATE_USER_IDS", {str(user.id)})
    monkeypatch.setattr(a.settings, "SHARED_ALLOWANCE_SCOPE", "beta")
    monkeypatch.setattr(a.settings, "BETA_ALLOWED_USER_IDS", {str(user.id)})
    monkeypatch.setattr(a.settings, "DEPLOYMENT_CHANNEL", "production")
    return session, user, subscriptions


@pytest.mark.asyncio
@pytest.mark.parametrize("name,plan", [("Close Friends Tier", "premium"), ("Katush Tier", "max"), ("Smooth tier", "premium")])
async def test_private_plan_capacity_and_monthly_renewal(db, monkeypatch, name, plan):
    s, u, _ = await enroll(db, monkeypatch, [name])
    row = await a.account(s, u.id, now=datetime(2026, 9, 21))
    assert row.plan == plan
    assert row.granted == BASE_GRANT * PLANS[plan]["multiple"]
    assert row.luna_granted == PLANS[plan]["luna_units"]
    row.spent = 100
    s.add(row)
    await s.commit()
    next_month = await a.account(s, u.id, now=datetime(2026, 10, 1))
    assert next_month.id != row.id
    assert next_month.spent == 0
    assert next_month.granted == row.granted


@pytest.mark.asyncio
async def test_beta_production_share_existing_spend_and_idempotency(db, monkeypatch):
    s, u, _ = await enroll(db, monkeypatch, ["Smooth tier"])
    monkeypatch.setattr(a.settings, "DEPLOYMENT_CHANNEL", "beta")
    original = await a.account(s, u.id)
    original.spent = 1234
    s.add(original)
    await s.commit()
    r = await a.reserve(s, user_id=u.id, conversation_id=None, request_id="both-surfaces", model="gpt-5.6-terra", ceiling=100)
    monkeypatch.setattr(a.settings, "DEPLOYMENT_CHANNEL", "production")
    same = await a.account(s, u.id)
    assert same.id == original.id and same.spent == 1234 and same.reserved == 100
    assert (await a.request_row(s, u.id, "both-surfaces")).id == r.id
    with pytest.raises(HTTPException) as error:
        await a.reserve(s, user_id=u.id, conversation_id=None, request_id="both-surfaces", model="gpt-5.6-terra", ceiling=100)
    assert error.value.status_code == 409
    assert len((await s.exec(select(AllowanceAccount))).all()) == 1
    await a.settle(s, u.id, "both-surfaces", success=False)
    assert same.reserved == 0


@pytest.mark.asyncio
async def test_overlapping_grants_and_downgrade_never_stack_or_refill(db, monkeypatch):
    s, u, subscriptions = await enroll(db, monkeypatch, ["Close Friends Tier", "Katush Tier"])
    row = await a.account(s, u.id, now=datetime(2026, 9, 21))
    assert row.plan == "max" and row.granted == 25_000_000
    row.spent = 7_000_000
    row.reserved = 100
    s.add(row)
    subscriptions[1].expires_at = datetime(2026, 9, 22)
    s.add(subscriptions[1])
    await s.commit()
    changed = await a.account(s, u.id, now=datetime(2026, 9, 23))
    assert changed.id == row.id and changed.plan == "premium"
    assert changed.granted == 7_000_100 and a.available(changed) == 0
    assert changed.spent == 7_000_000 and changed.reserved == 100
    await s.commit()
    await a.account(s, u.id, now=datetime(2026, 9, 23))
    adjustments = (await s.exec(select(AllowanceEvent).where(AllowanceEvent.kind == "plan_adjustment"))).all()
    assert len(adjustments) == 1 and adjustments[0].units == 7_000_100-25_000_000


@pytest.mark.asyncio
async def test_expired_private_subscription_does_not_receive_renewal(db, monkeypatch):
    s, u, subs = await enroll(db, monkeypatch, ["Smooth tier"])
    subs[0].expires_at = datetime(2026, 10, 1)
    s.add(subs[0])
    await s.commit()
    await a.account(s, u.id, now=datetime(2026, 9, 21))
    with pytest.raises(HTTPException) as error:
        await a.account(s, u.id, now=datetime(2026, 10, 1))
    assert error.value.status_code == 403
    assert len((await s.exec(select(AllowanceAccount))).all()) == 1


def test_production_rollout_is_explicit_and_beta_access_unchanged(monkeypatch):
    monkeypatch.setattr(a.settings, "SHARED_ALLOWANCE_ENABLED", True)
    monkeypatch.setattr(a.settings, "SHARED_ALLOWANCE_PRIVATE_USER_IDS", {"private"})
    monkeypatch.setattr(a.settings, "DEPLOYMENT_CHANNEL", "production")
    assert a.enabled("private") and not a.enabled("starter")
    monkeypatch.setattr(a.settings, "DEPLOYMENT_CHANNEL", "beta")
    monkeypatch.setattr(a.settings, "BETA_ALLOWED_USER_IDS", {"beta-user"})
    assert not a.enabled("private") and a.enabled("beta-user")


@pytest.mark.asyncio
async def test_enabling_starter_trials_keeps_private_capacity(db, monkeypatch):
    s, user, _ = await enroll(db, monkeypatch, ["Close Friends Tier"])
    monkeypatch.setattr(a.settings, "SHARED_ALLOWANCE_TRIAL_ENABLED", True)
    state = await a.snapshot(s, user.id)
    assert state["plan"] == "premium" and state["granted_units"] == 6_250_000
    assert state["trial"] is None and state["mode"] == "shared"
