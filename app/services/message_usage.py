"""Owner-scoped customer accounting for one saved answer; never supplier spend."""
from fastapi import HTTPException
from sqlmodel import select

from app.db.allowance import AllowanceAccount, AllowanceRequest
from app.db.models import Conversation, Message, RequestLedger
from app.schemas.message_usage import MessageUsage



async def message_usage(session, *, user_id, conversation_id, message_id):
    owned = (await session.exec(
        select(Message.id).join(Conversation, Conversation.id == Message.conversation_id)
        .where(Conversation.user_id == user_id, Conversation.id == conversation_id,
               Message.id == message_id, Message.role == "assistant")
    )).first()
    if owned is None:
        raise HTTPException(404, detail="Message not found")
    row = (await session.exec(
        select(AllowanceRequest, AllowanceAccount)
        .join(RequestLedger, RequestLedger.request_id == AllowanceRequest.request_id)
        .join(AllowanceAccount, AllowanceAccount.id == AllowanceRequest.account_id)
        .where(RequestLedger.user_id == user_id, RequestLedger.conversation_id == conversation_id,
               RequestLedger.assistant_message_id == message_id, RequestLedger.feature == "text",
               AllowanceRequest.user_id == user_id, AllowanceRequest.conversation_id == conversation_id,
               AllowanceAccount.user_id == user_id)
        .order_by(RequestLedger.created_at.desc(), AllowanceRequest.created_at.desc())
        .limit(1)
    )).first()
    if row is None:
        return MessageUsage(status="unavailable")
    request, account = row
    status = {"reserved": "in_progress", "pending": "pending", "complete": "complete", "failed": "failed"}.get(request.status)
    if status is None:
        return MessageUsage(status="unavailable")
    quote = request.customer_quote
    # An account can be upgraded or realigned; its current grant and dates cannot
    # reconstruct an old answer's admission basis.
    if not quote:
        return MessageUsage(status="unavailable")
    granted = quote["granted_units"]
    luna_granted = quote["luna_granted_units"]
    settled = status in ("complete", "failed")
    return MessageUsage(
        status=status,
        shared_percent=100 * request.charged / granted if settled and granted else None,
        luna_percent=100 * request.luna_charged / luna_granted if settled and luna_granted else None,
        estimated_min_percent=quote.get("estimated_min_percent"),
        estimated_max_percent=quote.get("estimated_max_percent"),
        maximum_percent=quote.get("maximum_percent"),
        basis="admission",
        period_start=quote.get("period_start"),
        period_end=quote.get("period_end"),
    )
