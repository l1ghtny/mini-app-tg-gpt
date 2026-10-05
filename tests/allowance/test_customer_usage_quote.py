import pytest
from app.core.config import settings
from app.services import allowance


@pytest.mark.asyncio
async def test_saved_customer_quote_keeps_its_grant_basis(db, monkeypatch):
    _, session, user = db
    monkeypatch.setattr(settings,"SHARED_ALLOWANCE_TRIAL_ENABLED",False)
    monkeypatch.setattr(settings,"SHARED_ALLOWANCE_GENERATION_V2_ENABLED",False)
    account = await allowance.account(session,user.id)
    original = account.granted
    period_start, period_end = account.period_start, account.period_end
    request = await allowance.reserve(session,user_id=user.id,conversation_id=None,request_id="quote-snapshot",
        model="gpt-5.6-terra",ceiling=100,customer_quote={"estimated_min_percent":0,"estimated_max_percent":1,"maximum_percent":None,
            "granted_units":original,"luna_granted_units":account.luna_granted,
            "period_start":period_start.replace(tzinfo=allowance.UTC).isoformat(),
            "period_end":period_end.replace(tzinfo=allowance.UTC).isoformat()})
    assert request.customer_quote["granted_units"] == original
    assert request.customer_quote["luna_granted_units"] == account.luna_granted
    assert request.customer_quote["period_start"] == period_start.replace(tzinfo=allowance.UTC).isoformat()
    assert request.customer_quote["period_end"] == period_end.replace(tzinfo=allowance.UTC).isoformat()
    account.granted *= 2
    session.add(account)
    await session.commit()
    await session.refresh(request)
    assert request.customer_quote["granted_units"] == original
    assert request.customer_quote["maximum_percent"] is None


@pytest.mark.asyncio
async def test_luna_snapshot_is_independent_and_handles_zero_grant(db,monkeypatch):
    _,session,user=db
    monkeypatch.setattr(settings,"SHARED_ALLOWANCE_TRIAL_ENABLED",False)
    monkeypatch.setattr(settings,"SHARED_ALLOWANCE_GENERATION_V2_ENABLED",False)
    account = await allowance.account(session,user.id)
    account.spent=account.granted
    account.luna_spent=account.luna_granted//4
    session.add(account)
    await session.commit()
    state=await allowance.snapshot(session,user.id)
    assert state["remaining_percent"]==0 and state["luna_remaining_percent"]==75
    assert state["luna_available"]
    account.luna_granted=account.luna_spent=0
    session.add(account)
    await session.commit()
    state=await allowance.snapshot(session,user.id)
    assert state["luna_remaining_percent"]==0 and not state["luna_available"]
