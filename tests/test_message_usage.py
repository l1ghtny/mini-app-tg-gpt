import uuid
from datetime import datetime

import pytest
from fastapi import HTTPException, Response
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.routes import get_message_usage
from app.db.database import engine
from app.db.models import AppUser, Conversation, Message, RequestLedger
from app.db.allowance import AllowanceAccount, AllowanceRequest
from app.services.message_usage import message_usage


async def seed(session, *, status="complete", quote=True):
    owner, other = AppUser(), AppUser()
    session.add_all([owner, other])
    await session.flush()
    chat = Conversation(user_id=owner.id, title="Usage test")
    foreign_chat = Conversation(user_id=other.id, title="Private")
    session.add_all([chat, foreign_chat])
    await session.flush()
    message = Message(conversation_id=chat.id, role="assistant")
    user_message = Message(conversation_id=chat.id, role="user")
    session.add_all([message, user_message])
    account = AllowanceAccount(user_id=owner.id, scope="production", plan="start",
        rate_version="test", granted=2000000, luna_granted=4000000,
        period_start=datetime(2026,9,1), period_end=datetime(2026,10,1))
    session.add(account)
    await session.flush()
    request = AllowanceRequest(account_id=account.id, user_id=owner.id, scope="production",
        request_id="request-usage", conversation_id=chat.id, model="gpt-5.6-luna",
        ceiling=50000, charged=1 if status == "complete" else 0,
        luna_charged=2000 if status == "complete" else 0, status=status,
        customer_quote={"granted_units": 1000000, "luna_granted_units": 2000000,
                        "estimated_min_percent": 0.1, "estimated_max_percent": 1.5,
                        "maximum_percent": 2, "period_start": "2026-09-01T00:00:00Z",
                        "period_end": "2026-10-01T00:00:00Z"} if quote else None,
        execution_plan={"supplier_ceiling":987654321,"risk_policy":{"secret":True}})
    ledger = RequestLedger(user_id=owner.id, conversation_id=chat.id, assistant_message_id=message.id,
        request_id=request.request_id, feature="text", model_name=request.model)
    session.add_all([request, ledger])
    await session.commit()
    return owner, other, chat, foreign_chat, message, user_message, request, account


@pytest.mark.asyncio
async def test_usage_is_owner_scoped_and_uses_immutable_quote_basis():
    async with AsyncSession(engine, expire_on_commit=False) as session:
        owner, other, chat, foreign, message, user_message, request, account = await seed(session)
        response = Response()
        result = await get_message_usage(chat.id, message.id, response, session, owner)
        assert result.status == "complete"
        assert result.shared_percent == 0.0001  # A positive charge never rounds to zero.
        assert result.luna_percent == 0.1
        assert result.maximum_percent == 2 and result.basis == "admission"
        assert result.period_start.year == 2026 and result.period_end.month == 10
        assert response.headers["cache-control"] == "private, no-store"
        account.granted *= 2
        account.luna_granted *= 2
        account.period_start = datetime(2026, 9, 22)
        account.period_end = datetime(2026, 10, 22)
        session.add(account)
        await session.commit()
        after = await message_usage(session,user_id=owner.id,conversation_id=chat.id,message_id=message.id)
        assert after == result
        payload = result.model_dump()
        assert not {"supplier_ceiling", "execution_plan", "risk_policy", "granted_units"} & payload.keys()
        for user_id, cid, mid in [(other.id,chat.id,message.id),(owner.id,foreign.id,message.id),
                                   (owner.id,chat.id,user_message.id),(owner.id,chat.id,uuid.uuid4())]:
            with pytest.raises(HTTPException) as exc:
                await message_usage(session,user_id=user_id,conversation_id=cid,message_id=mid)
            assert exc.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["reserved", "pending", "failed"])
async def test_unfinished_accounting_is_not_shown_as_zero_charge(status):
    async with AsyncSession(engine, expire_on_commit=False) as session:
        owner, _, chat, _, message, _, request, _ = await seed(session, status=status)
        result = await message_usage(session,user_id=owner.id,conversation_id=chat.id,message_id=message.id)
        assert result.status == {"reserved":"in_progress","pending":"pending","failed":"failed"}[status]
        assert (result.shared_percent == 0) == (status == "failed")
        assert (result.luna_percent == 0) == (status == "failed")


@pytest.mark.asyncio
async def test_legacy_usage_has_no_invented_quote_and_missing_ledger_is_unavailable():
    async with AsyncSession(engine, expire_on_commit=False) as session:
        owner, other, chat, _, message, _, request, account = await seed(session, quote=False)
        result = await message_usage(session,user_id=owner.id,conversation_id=chat.id,message_id=message.id)
        assert result.model_dump(exclude_none=True) == {"status":"unavailable"}
        account.granted *= 2
        account.period_start = datetime(2026, 9, 22)
        account.period_end = datetime(2026, 10, 22)
        session.add(account)
        await session.commit()
        after = await message_usage(session,user_id=owner.id,conversation_id=chat.id,message_id=message.id)
        assert after == result
        # A corrupt cross-owner relationship must not leak billing data.
        request.user_id = other.id
        session.add(request)
        await session.commit()
        result = await message_usage(session,user_id=owner.id,conversation_id=chat.id,message_id=message.id)
        assert result.model_dump(exclude_none=True) == {"status":"unavailable"}
