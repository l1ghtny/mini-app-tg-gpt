from __future__ import annotations

import asyncio
import io
import hashlib
import uuid
from typing import Optional, Tuple

from botocore.exceptions import ClientError
from fastapi import HTTPException
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from PIL import Image, ImageOps, UnidentifiedImageError

from app.r2.settings import Settings
from app.r2.client import R2_BUCKET
from app.r2.methods import head_object, get_bytes, put_bytes
from app.db.models import DerivedImage, ImageAsset
from app.core.config import settings
from app.services.image_assets import (
    IMAGE_STATUS_ACTIVE,
    IMAGE_STATUS_EXPIRED,
    IMAGE_STATUS_MISSING,
    IMAGE_STATUS_PROCESSING,
    effective_image_status,
    find_asset_by_url,
    mark_asset_status,
    refresh_processing_image_asset,
)
from app.services.public_image_reachability import ImageReachabilityError
from app.services.public_image_reachability import wait_for_image_url_reachability


logger = settings.custom_logger


# Enable HEIC/HEIF if pillow-heif is installed
try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:
    raise ImportError("pillow-heif is not installed")


SUPPORTED_DIRECT = {"image/png", "image/jpeg", "image/webp", "image/gif"}
# Preserve original detail up to the provider's per-image rejection limits.
# If this budget changes, invalidate the cached variants for MAX_IMAGE_SIDE.
MAX_IMAGE_PATCHES = 30_000
MAX_IMAGE_SIDE = 65_535
IMAGE_PATCH_SIDE = 32


def _is_not_found_client_error(exc: ClientError) -> bool:
    error = exc.response.get("Error") or {}
    code = str(error.get("Code") or "").strip()
    status_code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return status_code == 404 or code in {"404", "NoSuchKey", "NotFound"}


def _normalize_public_base_url(base_url: str | None) -> str | None:
    if not base_url:
        return None
    return base_url if base_url.endswith("/") else f"{base_url}/"


def _user_public_base_url() -> str:
    return _normalize_public_base_url(Settings.R2_PUBLIC_BASE_URL) or ""


def _openai_public_base_url() -> str:
    return (
        _normalize_public_base_url(Settings.R2_OPENAI_PUBLIC_BASE_URL)
        or _user_public_base_url()
    )


def _known_public_base_urls() -> tuple[str, ...]:
    known: list[str] = []
    for raw_base_url in (
        Settings.R2_PUBLIC_BASE_URL,
        Settings.R2_OPENAI_PUBLIC_BASE_URL,
    ):
        normalized = _normalize_public_base_url(raw_base_url)
        if normalized and normalized not in known:
            known.append(normalized)
    return tuple(known)


def _strip_legacy_bucket_prefix(path: str) -> str:
    bucket_prefix = f"{R2_BUCKET}/"
    if path.startswith(bucket_prefix):
        return path[len(bucket_prefix) :]
    return path


def _public_url(key: str, *, for_openai: bool = False) -> str:
    base_url = _openai_public_base_url() if for_openai else _user_public_base_url()
    return f"{base_url}{key}"


def _key_from_public_url(url: str) -> Optional[str]:
    for public_base_url in _known_public_base_urls():
        if url.startswith(public_base_url):
            return _strip_legacy_bucket_prefix(url[len(public_base_url) :])
    return None  # external URL or a different domain -> pass through as-is


def _decide_target(mime: str, has_alpha: bool) -> str:
    if mime in {"image/png", "image/gif"}:
        return "png"
    if mime == "image/webp":
        return "webp"
    return "png" if has_alpha else "jpeg"


def _fit_image_dimensions(width: int, height: int, max_side: int) -> tuple[int, int]:
    """Find the largest proportional size that fits both limits, without upscaling."""
    if min(width, height, max_side) < 1:
        raise ValueError("Image dimensions and max_side must be positive")
    longest = max(width, height)

    def dimensions(side: int) -> tuple[int, int]:
        return max(1, width * side // longest), max(1, height * side // longest)

    low, high = 1, min(longest, max_side, MAX_IMAGE_SIDE)
    while low < high:
        side = (low + high + 1) // 2
        w, h = dimensions(side)
        patches = ((w + IMAGE_PATCH_SIDE - 1) // IMAGE_PATCH_SIDE) * (
            (h + IMAGE_PATCH_SIDE - 1) // IMAGE_PATCH_SIDE
        )
        if patches <= MAX_IMAGE_PATCHES:
            low = side
        else:
            high = side - 1
    return dimensions(low)


def _flatten_alpha_to_rgb(im: Image.Image) -> Image.Image:
    # JPEG can't store alpha; flatten to white
    if im.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        return bg
    if im.mode == "P" and "transparency" in im.info:
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        return bg
    return im.convert("RGB")


def _transcode(data: bytes, target: str, max_side: int) -> Tuple[bytes, str, bool]:
    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im)
        size = _fit_image_dimensions(*im.size, max_side)
        if im.size != size:
            if im.mode == "P":
                im = im.convert("RGBA" if "transparency" in im.info else "RGB")
            im = im.resize(size, Image.Resampling.LANCZOS)
        has_alpha = (im.mode in ("RGBA", "LA")) or ("transparency" in im.info)
        buf = io.BytesIO()
        if target == "jpeg":
            im = _flatten_alpha_to_rgb(im)
            im.save(buf, format="JPEG", quality=95, subsampling=0, optimize=True)
            return buf.getvalue(), "image/jpeg", has_alpha
        elif target == "png":
            im.save(buf, format="PNG", optimize=True)
            return buf.getvalue(), "image/png", has_alpha
        elif target == "webp":
            im.save(buf, format="WEBP", lossless=True, method=4)
            return buf.getvalue(), "image/webp", has_alpha
        else:
            raise ValueError(f"Unsupported target: {target}")


def _derive_image_sync(
    original: bytes, mime: str, max_size: int
) -> tuple[bytes, str, str] | None:
    with Image.open(io.BytesIO(original)) as im:
        # Inspect actual image content even when storage metadata claims a supported format.
        mime = Image.MIME.get(im.format, mime)
        has_alpha = (im.mode in ("RGBA", "LA")) or ("transparency" in im.info)
        if (
            mime in SUPPORTED_DIRECT
            and _fit_image_dimensions(*im.size, max_size) == im.size
            and not getattr(im, "is_animated", False)
        ):
            return None

    target = _decide_target(mime, has_alpha)
    converted, converted_mime, _ = _transcode(
        original, target=target, max_side=max_size
    )
    return converted, converted_mime, target


async def require_owned_image_asset(
    session: AsyncSession, url: str, *, user_id: uuid.UUID
) -> ImageAsset:
    asset = await find_asset_by_url(session, url, user_id=user_id)
    if asset is None:
        key = _key_from_public_url(url)
        if key:
            asset = (
                await session.exec(
                    select(ImageAsset)
                    .where(
                        ImageAsset.user_id == user_id,
                        ImageAsset.bucket == R2_BUCKET,
                        ImageAsset.key == key,
                    )
                    .order_by(ImageAsset.created_at.desc())
                )
            ).first()
    # Domain recognition alone is not authorization to access an R2 object.
    if asset is None or asset.user_id != user_id or asset.bucket != R2_BUCKET:
        raise HTTPException(status_code=403, detail="image_attachment_not_allowed")
    return asset


async def ensure_openai_compatible_image_url(
    session: AsyncSession,
    url_or_key: str,
    *,
    user_id: uuid.UUID,
    max_size: int = MAX_IMAGE_SIDE,
) -> str:
    """
    Preserve supported originals within the dimension and patch limits; otherwise
    cache a resized/converted copy. Only the authenticated owner's managed R2
    assets are accepted; arbitrary URLs and unowned objects never reach a provider.
    """
    asset = await require_owned_image_asset(session, url_or_key, user_id=user_id)
    if asset:
        status = effective_image_status(asset)
        if status == IMAGE_STATUS_PROCESSING:
            await refresh_processing_image_asset(
                session,
                asset,
                logger=logger,
                force=True,
                max_retries=3,
                delay=0.5,
                min_recheck_seconds=0,
            )
            status = effective_image_status(asset)
        if status == IMAGE_STATUS_EXPIRED:
            if asset.status != IMAGE_STATUS_EXPIRED:
                await mark_asset_status(session, asset, IMAGE_STATUS_EXPIRED)
            raise HTTPException(status_code=410, detail="Image expired")
        if status == IMAGE_STATUS_PROCESSING:
            raise HTTPException(status_code=409, detail="image_not_ready")
        if status != IMAGE_STATUS_ACTIVE:
            raise HTTPException(status_code=410, detail="Image unavailable")

    key = asset.key

    openai_url = _public_url(key, for_openai=True)

    # HEAD -> content-type
    try:
        meta = await head_object(key)
    except ClientError as exc:
        if _is_not_found_client_error(exc):
            if asset and asset.status != IMAGE_STATUS_MISSING:
                await mark_asset_status(session, asset, IMAGE_STATUS_MISSING)
            raise HTTPException(status_code=410, detail="Image unavailable") from exc
        raise
    mime = (meta.get("ContentType") or "application/octet-stream").lower()

    # See if we already have a derived variant
    res = await session.exec(
        select(DerivedImage).where(
            DerivedImage.original_key == key,
            DerivedImage.max_side == max_size,
        )
    )
    row = res.first()
    if row:
        derived_openai_url = _public_url(row.derived_key, for_openai=True)
        try:
            await wait_for_image_url_reachability(
                derived_openai_url, logger=logger, require_success=True
            )
        except ImageReachabilityError as exc:
            raise HTTPException(status_code=409, detail="image_not_ready") from exc
        return derived_openai_url

    # Pull original bytes, transcode, and store.
    # Offload PIL decode/transcode to a worker thread to avoid blocking the event loop.
    try:
        original = await get_bytes(key)
    except ClientError as exc:
        if _is_not_found_client_error(exc):
            if asset and asset.status != IMAGE_STATUS_MISSING:
                await mark_asset_status(session, asset, IMAGE_STATUS_MISSING)
            raise HTTPException(status_code=410, detail="Image unavailable") from exc
        raise
    try:
        derived = await asyncio.to_thread(
            _derive_image_sync,
            original,
            mime,
            max_size,
        )
    except Image.DecompressionBombError as exc:
        raise HTTPException(
            status_code=413, detail="Image is too large to process"
        ) from exc
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=400, detail="Invalid image") from exc
    if derived is None:
        try:
            await wait_for_image_url_reachability(
                openai_url, logger=logger, require_success=True
            )
        except ImageReachabilityError as exc:
            raise HTTPException(status_code=409, detail="image_not_ready") from exc
        return openai_url
    converted, converted_mime, target = derived

    # Distinct originals must not collide with derived_key's unique constraint.
    digest = hashlib.sha256(key.encode())
    digest.update(b"\0")
    digest.update(converted)
    sha = digest.hexdigest()
    ext = (
        ".jpg"
        if converted_mime == "image/jpeg"
        else ".png"
        if converted_mime == "image/png"
        else ".webp"
    )
    derived_key = f"derived/{sha[:2]}/{sha}{ext}"

    await put_bytes(
        derived_key,
        converted,
        content_type=converted_mime,
        metadata={"source": "derived"},
    )
    await session.exec(
        insert(DerivedImage)
        .values(
            original_key=key,
            target_format=target,
            max_side=max_size,
            derived_key=derived_key,
        )
        .on_conflict_do_nothing(constraint="uq_derived_image_variant")
    )
    await session.commit()

    derived_openai_url = _public_url(derived_key, for_openai=True)
    try:
        await wait_for_image_url_reachability(
            derived_openai_url, logger=logger, require_success=True
        )
    except ImageReachabilityError as exc:
        raise HTTPException(status_code=409, detail="image_not_ready") from exc

    return derived_openai_url
