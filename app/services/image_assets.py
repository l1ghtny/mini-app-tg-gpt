from __future__ import annotations

import os
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import ImageAsset, Message, MessageContent, utcnow_naive
from app.db.subscription_tiers import UsagePackSource
from app.r2.client import R2_BUCKET
from app.r2.settings import Settings
from app.services.subscription_check.entitlements import get_active_subscriptions, get_active_usage_packs


IMAGE_STATUS_ACTIVE = "active"
IMAGE_STATUS_EXPIRED = "expired"
IMAGE_STATUS_MISSING = "missing"
IMAGE_STATUS_DELETED = "deleted"

IMAGE_SOURCE_GENERATED = "generated"
IMAGE_SOURCE_UPLOADED = "uploaded"
IMAGE_SOURCE_DERIVED = "derived"


def _retention_days_from_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def free_retention_days() -> int:
    return _retention_days_from_env("IMAGE_FREE_RETENTION_DAYS", 30)


def paid_retention_days() -> int:
    return _retention_days_from_env("IMAGE_PAID_RETENTION_DAYS", 365)


def partial_retention_days() -> int:
    return _retention_days_from_env("IMAGE_PARTIAL_RETENTION_DAYS", 1)


def _policy_name(kind: str, days: int) -> str:
    if days <= 0:
        return f"{kind}_permanent"
    return f"{kind}_{days}d"


async def user_has_paid_image_retention(session: AsyncSession, user_id: uuid.UUID) -> bool:
    subscriptions = await get_active_subscriptions(session, user_id)
    for subscription in subscriptions:
        tier = getattr(subscription, "tier", None)
        if tier and (tier.price_cents or 0) > 0:
            return True

    packs = await get_active_usage_packs(session, user_id)
    for user_pack in packs:
        pack = getattr(user_pack, "pack", None)
        if user_pack.source == UsagePackSource.paid and pack and (pack.price_cents or 0) > 0:
            return True

    return False


async def resolve_image_retention(
    session: AsyncSession,
    user_id: uuid.UUID,
) -> tuple[str, datetime | None, str]:
    paid = await user_has_paid_image_retention(session, user_id)
    kind = "paid" if paid else "free"
    days = paid_retention_days() if paid else free_retention_days()
    expires_at = None if days <= 0 else utcnow_naive() + timedelta(days=days)
    return _policy_name(kind, days), expires_at, kind


async def object_prefix_for_user(
    session: AsyncSession,
    user_id: uuid.UUID,
    source: str,
) -> str:
    _, _, kind = await resolve_image_retention(session, user_id)
    source_part = source.strip().lower() or IMAGE_SOURCE_GENERATED
    return f"images/{kind}/{source_part}"


def partial_object_prefix() -> str:
    return "images/partial"


def public_url_for_key(bucket: str, key: str) -> str:
    return f"{Settings.R2_PUBLIC_BASE_URL}{bucket}/{key}"


def key_from_public_url(url: str | None) -> str | None:
    if not url:
        return None
    base = f"{Settings.R2_PUBLIC_BASE_URL}{R2_BUCKET}/"
    if url.startswith(base):
        return url[len(base):]

    parsed_base = urlsplit(base)
    parsed = urlsplit(url)
    if parsed.scheme in {"http", "https"} and parsed.hostname and parsed.hostname == parsed_base.hostname:
        path = parsed.path.lstrip("/")
        bucket_prefix = f"{R2_BUCKET}/"
        if path.startswith(bucket_prefix):
            return path[len(bucket_prefix):]
    return None


def effective_image_status(asset: ImageAsset, now: datetime | None = None) -> str:
    if asset.status != IMAGE_STATUS_ACTIVE:
        return asset.status
    moment = now or utcnow_naive()
    if asset.expires_at and asset.expires_at <= moment:
        return IMAGE_STATUS_EXPIRED
    return IMAGE_STATUS_ACTIVE


def serialize_image_asset(asset: ImageAsset | None) -> dict | None:
    if asset is None:
        return None

    status = effective_image_status(asset)
    unavailable_reason = None if status == IMAGE_STATUS_ACTIVE else status
    return {
        "id": str(asset.id),
        "url": asset.public_url,
        "status": status,
        "expires_at": asset.expires_at.isoformat(timespec="seconds") if asset.expires_at else None,
        "retention_policy": asset.retention_policy,
        "source": asset.source,
        "unavailable_reason": unavailable_reason,
    }


def attach_image_asset_to_content(content: MessageContent, asset: ImageAsset | None) -> None:
    if content.type != "image_url" or asset is None:
        return

    payload = dict(content.data or {})
    payload["image"] = serialize_image_asset(asset)
    content.data = payload


async def create_image_asset(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    public_url: str,
    bucket: str | None = None,
    key: str | None = None,
    source: str = IMAGE_SOURCE_GENERATED,
    conversation_id: uuid.UUID | None = None,
    message_content: MessageContent | None = None,
) -> ImageAsset:
    retention_policy, expires_at, _ = await resolve_image_retention(session, user_id)
    asset = ImageAsset(
        user_id=user_id,
        conversation_id=conversation_id,
        message_content_id=message_content.id if message_content else None,
        bucket=bucket or R2_BUCKET,
        key=key or key_from_public_url(public_url) or "",
        public_url=public_url,
        source=source,
        retention_policy=retention_policy,
        expires_at=expires_at,
        status=IMAGE_STATUS_ACTIVE,
    )
    session.add(asset)
    await session.flush()

    if message_content is not None:
        attach_image_asset_to_content(message_content, asset)
        session.add(message_content)

    return asset


async def find_asset_by_url(
    session: AsyncSession,
    url: str,
    *,
    user_id: uuid.UUID | None = None,
) -> ImageAsset | None:
    q = select(ImageAsset).where(ImageAsset.public_url == url)
    if user_id is not None:
        q = q.where(ImageAsset.user_id == user_id)
    q = q.order_by(ImageAsset.created_at.desc())
    return (await session.exec(q)).first()


async def find_asset_by_id_or_content_id(
    session: AsyncSession,
    image_id: uuid.UUID,
    *,
    user_id: uuid.UUID | None = None,
) -> tuple[ImageAsset | None, MessageContent | None]:
    q = select(ImageAsset).where(ImageAsset.id == image_id)
    if user_id is not None:
        q = q.where(ImageAsset.user_id == user_id)
    asset = (await session.exec(q)).first()
    if asset:
        content = await session.get(MessageContent, asset.message_content_id) if asset.message_content_id else None
        return asset, content

    content = await session.get(MessageContent, image_id)
    if not content or content.type != "image_url":
        return None, None

    message = None
    if user_id is not None or asset:
        message_q = (
            select(Message)
            .where(Message.id == content.message_id)
            .options(selectinload(Message.conversation))
        )
        message = (await session.exec(message_q)).first()

    if user_id is not None:
        if not message or not message.conversation or message.conversation.user_id != user_id:
            return None, None

    asset = await find_asset_by_url(session, content.value, user_id=user_id)
    if asset and asset.message_content_id is None:
        asset.message_content_id = content.id
        asset.conversation_id = message.conversation_id if message else asset.conversation_id
        attach_image_asset_to_content(content, asset)
        session.add(asset)
        session.add(content)
        await session.commit()
        await session.refresh(asset)
        await session.refresh(content)

    return asset, content


async def link_content_to_existing_asset_by_url(
    session: AsyncSession,
    content: MessageContent,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID | None = None,
) -> ImageAsset | None:
    if content.type != "image_url":
        return None

    asset = await find_asset_by_url(session, content.value, user_id=user_id)
    if not asset:
        return None

    asset.message_content_id = content.id
    asset.conversation_id = conversation_id or asset.conversation_id
    attach_image_asset_to_content(content, asset)
    session.add(asset)
    session.add(content)
    return asset


async def detach_assets_from_message_content_ids(
    session: AsyncSession,
    content_ids: Sequence[uuid.UUID],
) -> None:
    if not content_ids:
        return

    assets = (
        await session.exec(
            select(ImageAsset).where(ImageAsset.message_content_id.in_(list(content_ids)))
        )
    ).all()
    for asset in assets:
        asset.message_content_id = None
        session.add(asset)


async def mark_asset_status(
    session: AsyncSession,
    asset: ImageAsset,
    status: str,
) -> None:
    asset.status = status
    asset.last_checked_at = utcnow_naive()
    if status in {IMAGE_STATUS_DELETED, IMAGE_STATUS_MISSING, IMAGE_STATUS_EXPIRED} and asset.deleted_at is None:
        asset.deleted_at = utcnow_naive() if status in {IMAGE_STATUS_DELETED, IMAGE_STATUS_MISSING} else asset.deleted_at
    session.add(asset)
    await session.commit()
