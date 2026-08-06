import json
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.dependencies import get_current_user
from app.api.document_helpers import delete_document
from app.db import models
from app.db.database import get_session
from app.db.models import AppUser
from app.db.subscription_tiers import UserSubscription, UserUsagePack
from app.r2.methods import delete_object

account = APIRouter(prefix="/account", tags=["account"])


class DeleteAccountRequest(BaseModel):
    confirmation: Literal["DELETE"]


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "hex"):
        return str(value)
    return value


def _safe_row(row: Any, *, exclude: set[str] | None = None) -> dict[str, Any]:
    blocked = exclude or set()
    return {
        column.name: _json_value(getattr(row, column.name))
        for column in row.__table__.columns
        if column.name not in blocked
    }


@account.get("/export")
async def export_account_data(
    current_user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    conversations = (
        await session.exec(
            select(models.Conversation)
            .where(models.Conversation.user_id == current_user.id)
            .options(
                selectinload(models.Conversation.messages).selectinload(models.Message.content)
            )
        )
    ).all()
    identities = (
        await session.exec(
            select(models.UserIdentity).where(models.UserIdentity.user_id == current_user.id)
        )
    ).all()

    payload = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "account": _safe_row(current_user),
        "identities": [_safe_row(item) for item in identities],
        "folders": [
            _safe_row(item)
            for item in (
                await session.exec(select(models.ChatFolder).where(models.ChatFolder.user_id == current_user.id))
            ).all()
        ],
        "conversations": [
            {
                **_safe_row(conversation),
                "messages": [
                    {
                        **_safe_row(message),
                        "content": [_safe_row(part) for part in message.content],
                    }
                    for message in conversation.messages
                ],
            }
            for conversation in conversations
        ],
        "documents": [
            _safe_row(
                item,
                exclude={
                    "openai_file_id",
                    "openai_vector_store_id",
                    "sha256",
                    "source_bucket",
                    "source_storage_key",
                },
            )
            for item in (
                await session.exec(select(models.UserDocument).where(models.UserDocument.user_id == current_user.id))
            ).all()
        ],
        "subscriptions": [
            _safe_row(item)
            for item in (
                await session.exec(select(UserSubscription).where(UserSubscription.user_id == current_user.id))
            ).all()
        ],
        "usage_packs": [
            _safe_row(item)
            for item in (
                await session.exec(select(UserUsagePack).where(UserUsagePack.user_id == current_user.id))
            ).all()
        ],
        "payments": [
            _safe_row(item, exclude={"bound_method_snapshot"})
            for item in (
                await session.exec(select(models.Payment).where(models.Payment.user_id == current_user.id))
            ).all()
        ],
        "usage_ledger": [
            _safe_row(item)
            for item in (
                await session.exec(select(models.RequestLedger).where(models.RequestLedger.user_id == current_user.id))
            ).all()
        ],
    }
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="lightny-account-export.json"'},
    )


@account.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    payload: DeleteAccountRequest,
    background_tasks: BackgroundTasks,
    current_user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    if payload.confirmation != "DELETE":
        raise HTTPException(status_code=422, detail="deletion_confirmation_required")

    conversations = (
        await session.exec(
            select(models.Conversation)
            .where(models.Conversation.user_id == current_user.id)
            .options(
                selectinload(models.Conversation.messages).selectinload(models.Message.content),
                selectinload(models.Conversation.attached_documents),
            )
        )
    ).all()
    for conversation in conversations:
        await session.delete(conversation)

    folders = (
        await session.exec(
            select(models.ChatFolder)
            .where(models.ChatFolder.user_id == current_user.id)
            .options(
                selectinload(models.ChatFolder.attached_documents),
                selectinload(models.ChatFolder.conversations),
            )
        )
    ).all()
    for folder in folders:
        await session.delete(folder)

    documents = (
        await session.exec(
            select(models.UserDocument).where(
                models.UserDocument.user_id == current_user.id,
                models.UserDocument.deleted_at.is_(None),
            )
        )
    ).all()
    for document in documents:
        await delete_document(
            session=session,
            user=current_user,
            document_id=document.id,
            background_tasks=background_tasks,
        )

    image_assets = (
        await session.exec(select(models.ImageAsset).where(models.ImageAsset.user_id == current_user.id))
    ).all()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for asset in image_assets:
        if asset.status != "deleted":
            background_tasks.add_task(delete_object, asset.key)
        asset.status = "deleted"
        asset.deleted_at = now
        asset.conversation_id = None
        asset.message_content_id = None
        session.add(asset)

    subscriptions = (
        await session.exec(select(UserSubscription).where(UserSubscription.user_id == current_user.id))
    ).all()
    for subscription in subscriptions:
        subscription.auto_renew_enabled = False
        session.add(subscription)

    methods = (
        await session.exec(select(models.PaymentMethod).where(models.PaymentMethod.user_id == current_user.id))
    ).all()
    for method in methods:
        method.status = models.PaymentMethodStatus.detached.value
        method.is_default = False
        method.rebill_id = None
        method.account_token = None
        method.phone = None
        method.pan = "****"
        method.exp_date = ""
        method.detached_at = now
        session.add(method)

    identities = (
        await session.exec(select(models.UserIdentity).where(models.UserIdentity.user_id == current_user.id))
    ).all()
    for identity in identities:
        await session.delete(identity)
    browser_sessions = (
        await session.exec(select(models.BrowserSession).where(models.BrowserSession.user_id == current_user.id))
    ).all()
    for browser_session in browser_sessions:
        browser_session.revoked_at = now
        session.add(browser_session)

    current_user.deleted_at = now
    current_user.telegram_id = None
    current_user.telegram_username = None
    current_user.telegram_first_name = None
    current_user.telegram_last_name = None
    current_user.campaign = None
    current_user.default_prompt = ""
    session.add(current_user)
    await session.commit()
