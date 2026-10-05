from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid
import pytest
from app.db.allowance import AllowanceAccount
from app.services import allowance, allowance_chat, allowance_tasks
from app.services.allowance_policy import RATE_VERSION, PLANS, grant_units
from app.schemas.chat import NewMessageRequest

@pytest.mark.asyncio
async def test_quote_and_charge_use_same_grant_after_tier_expiry(monkeypatch):
    from app.api import chat_helpers
    a = AllowanceAccount(user_id=uuid.uuid4(),scope='production',plan='max',rate_version=RATE_VERSION,
        granted=grant_units('max',version=RATE_VERSION),luna_granted=PLANS['max']['luna_units'],
        period_start=datetime(2026,10,1),period_end=datetime(2026,11,1))
    monkeypatch.setattr(allowance.settings,'SHARED_ALLOWANCE_GENERATION_V2_ENABLED',False)
    monkeypatch.setattr(allowance,'require_enabled',lambda _:None)
    monkeypatch.setattr(allowance,'account',AsyncMock(return_value=a))
    monkeypatch.setattr(allowance_tasks,'lock',AsyncMock())
    monkeypatch.setattr(allowance_tasks,'slots',AsyncMock(return_value=(0,False)))
    monkeypatch.setattr(chat_helpers,'_build_history_for_openai',AsyncMock(return_value=[]))
    monkeypatch.setattr(chat_helpers,'_resolve_system_prompt',lambda *_:'Be helpful.')
    s=SimpleNamespace(exec=AsyncMock(return_value=SimpleNamespace(all=lambda:[],first=lambda:None)),
        commit=AsyncMock(),flush=AsyncMock(),add=lambda _:None)
    u=SimpleNamespace(id=a.user_id)
    c=SimpleNamespace(id=uuid.uuid4(),history_summary=None,image_quality='low')
    req=NewMessageRequest(role='user',model='gpt-5.6-terra',content=[{'type':'text','value':'Draw a cup.'}],
        tool_choice=['image_generation'],required_tool='image_generation',image_quality='low',
        client_request_id='quote-race')
    e=await allowance_chat.estimate(s,u,c,req)
    req.estimate_reference=e['estimate_reference']
    # The actual estimate commits before reserve reacquires the account. A
    # supported overlapping Max invitation can expire in that unlocked gap.
    async def expire_high_tier():
        a.plan='premium'
        a.granted=grant_units('premium',version=RATE_VERSION)
        a.luna_granted=PLANS['premium']['luna_units']
    s.commit.side_effect=expire_high_tier
    r=await allowance_chat.admit(s,u,c,req)
    max_in_saved_basis=100*r.ceiling/r.customer_quote['granted_units']
    print('stored maximum_percent:',r.customer_quote['maximum_percent'],
        'actual ceiling in saved grant basis:',max_in_saved_basis)
    assert abs(r.customer_quote['maximum_percent']-max_in_saved_basis)<=0.005
