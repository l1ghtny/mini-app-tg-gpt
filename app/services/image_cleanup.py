from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Iterable, Mapping, Sequence

from botocore.exceptions import ClientError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import DerivedImage, ImageAsset, MessageContent, utcnow_naive


@dataclass(frozen=True)
class BucketObject:
    key: str
    size: int
    etag: str
    last_modified: datetime


@dataclass(frozen=True)
class CleanupCandidate:
    object: BucketObject
    reason: str


@dataclass(frozen=True)
class ImageCleanupResult:
    scanned: int
    candidates: int
    deleted: int
    deleted_bytes: int
    asset_rows_updated: int
    skipped: int


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def orphan_policy_allows(
    key: str,
    *,
    last_modified: datetime,
    now: datetime,
    partial_days: int,
    free_days: int,
    paid_days: int,
) -> bool:
    age = _aware_utc(now) - _aware_utc(last_modified)
    if key.startswith("images/partial/"):
        return age >= timedelta(days=partial_days)
    if key.startswith("images/free/"):
        return age >= timedelta(days=free_days)
    if key.startswith("images/paid/"):
        return age >= timedelta(days=paid_days)
    return False


def select_cleanup_candidates(
    objects: Sequence[BucketObject],
    *,
    assets_by_key: Mapping[str, Sequence[ImageAsset]],
    message_keys: set[str],
    derived_keys: set[str],
    now: datetime,
    detached_grace_hours: int,
    partial_days: int,
    free_days: int,
    paid_days: int,
) -> list[CleanupCandidate]:
    grace = timedelta(hours=detached_grace_hours)
    candidates: list[CleanupCandidate] = []
    for item in objects:
        if item.key in message_keys or item.key in derived_keys:
            continue
        rows = list(assets_by_key.get(item.key, ()))
        if rows:
            if not all(
                row.conversation_id is None and row.message_content_id is None
                for row in rows
            ):
                continue
            if _aware_utc(now) - _aware_utc(item.last_modified) < grace:
                continue
            candidates.append(CleanupCandidate(object=item, reason="detached_asset"))
            continue
        if orphan_policy_allows(
            item.key,
            last_modified=item.last_modified,
            now=now,
            partial_days=partial_days,
            free_days=free_days,
            paid_days=paid_days,
        ):
            candidates.append(CleanupCandidate(object=item, reason="orphan"))
    return candidates


async def _list_bucket_objects() -> list[BucketObject]:
    from app.r2.client import R2_BUCKET, s3_client

    objects: list[BucketObject] = []
    continuation_token: str | None = None
    async with s3_client() as s3:
        while True:
            kwargs: dict[str, object] = {"Bucket": R2_BUCKET, "MaxKeys": 1000}
            if continuation_token:
                kwargs["ContinuationToken"] = continuation_token
            page = await s3.list_objects_v2(**kwargs)
            for item in page.get("Contents", []):
                objects.append(
                    BucketObject(
                        key=item["Key"],
                        size=int(item.get("Size", 0)),
                        etag=str(item.get("ETag", "")).strip('"'),
                        last_modified=_aware_utc(item["LastModified"]),
                    )
                )
            if not page.get("IsTruncated"):
                break
            continuation_token = page.get("NextContinuationToken")
            if not continuation_token:
                raise RuntimeError(
                    "R2 returned a truncated page without a continuation token"
                )
    return objects


def _group_assets_by_key(assets: Iterable[ImageAsset]) -> dict[str, list[ImageAsset]]:
    grouped: dict[str, list[ImageAsset]] = defaultdict(list)
    for asset in assets:
        if asset.key:
            grouped[asset.key].append(asset)
    return grouped


async def cleanup_image_storage(
    database_url: str,
    *,
    dry_run: bool = False,
    limit: int = 500,
    detached_grace_hours: int = 48,
    partial_days: int = 1,
    free_days: int = 30,
    paid_days: int = 365,
) -> ImageCleanupResult:
    from app.r2.client import R2_BUCKET, s3_client
    from app.services.image_assets import key_from_public_url

    now = datetime.now(UTC)
    objects = await _list_bucket_objects()
    engine = create_async_engine(database_url, future=True, echo=False)
    deleted = deleted_bytes = asset_rows_updated = skipped = 0
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            assets = (await session.exec(select(ImageAsset))).all()
            message_urls = (
                await session.exec(
                    select(MessageContent.value).where(
                        MessageContent.type == "image_url"
                    )
                )
            ).all()
            derived_rows = (await session.exec(select(DerivedImage))).all()
            message_keys = {
                key
                for url in message_urls
                if (key := key_from_public_url(url)) is not None
            }
            derived_keys = {
                value
                for row in derived_rows
                for value in (row.original_key, row.derived_key)
                if value
            }
            assets_by_key = _group_assets_by_key(assets)
            candidates = select_cleanup_candidates(
                objects,
                assets_by_key=assets_by_key,
                message_keys=message_keys,
                derived_keys=derived_keys,
                now=now,
                detached_grace_hours=detached_grace_hours,
                partial_days=partial_days,
                free_days=free_days,
                paid_days=paid_days,
            )
            candidates.sort(key=lambda candidate: candidate.object.last_modified)
            candidates = candidates[:limit]

            async with s3_client() as s3:
                for candidate in candidates:
                    item = candidate.object
                    current_assets = (
                        await session.exec(
                            select(ImageAsset)
                            .where(ImageAsset.key == item.key)
                            .with_for_update()
                        )
                    ).all()
                    if current_assets and not all(
                        asset.conversation_id is None
                        and asset.message_content_id is None
                        for asset in current_assets
                    ):
                        await session.rollback()
                        skipped += 1
                        continue
                    current_urls = {asset.public_url for asset in current_assets}
                    if current_urls:
                        message_reference = (
                            await session.exec(
                                select(MessageContent.id).where(
                                    MessageContent.type == "image_url",
                                    MessageContent.value.in_(current_urls),
                                )
                            )
                        ).first()
                        if message_reference:
                            await session.rollback()
                            skipped += 1
                            continue

                    try:
                        head = await s3.head_object(Bucket=R2_BUCKET, Key=item.key)
                    except ClientError as exc:
                        error_code = str(
                            exc.response.get("Error", {}).get("Code", "")
                        )
                        if error_code in {"404", "NoSuchKey", "NotFound"}:
                            for asset in current_assets:
                                asset.status = "deleted"
                                asset.deleted_at = asset.deleted_at or utcnow_naive()
                                asset.last_checked_at = utcnow_naive()
                                session.add(asset)
                            if current_assets and not dry_run:
                                await session.commit()
                                asset_rows_updated += len(current_assets)
                            else:
                                await session.rollback()
                            continue
                        await session.rollback()
                        skipped += 1
                        continue

                    current_size = int(head.get("ContentLength", -1))
                    current_etag = str(head.get("ETag", "")).strip('"')
                    if current_size != item.size or current_etag != item.etag:
                        await session.rollback()
                        skipped += 1
                        continue
                    if dry_run:
                        await session.rollback()
                        continue

                    await s3.delete_object(Bucket=R2_BUCKET, Key=item.key)
                    timestamp = utcnow_naive()
                    for asset in current_assets:
                        asset.status = "deleted"
                        asset.deleted_at = asset.deleted_at or timestamp
                        asset.last_checked_at = timestamp
                        asset.conversation_id = None
                        asset.message_content_id = None
                        session.add(asset)
                    if current_assets:
                        await session.commit()
                        asset_rows_updated += len(current_assets)
                    else:
                        await session.rollback()
                    deleted += 1
                    deleted_bytes += current_size
    finally:
        await engine.dispose()

    return ImageCleanupResult(
        scanned=len(objects),
        candidates=min(len(candidates), limit),
        deleted=deleted,
        deleted_bytes=deleted_bytes,
        asset_rows_updated=asset_rows_updated,
        skipped=skipped,
    )
