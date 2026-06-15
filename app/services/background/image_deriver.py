from __future__ import annotations

import asyncio
import io
import hashlib
from typing import Optional, Tuple

from fastapi import HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from PIL import Image, ImageOps

from app.r2.settings import Settings
from app.r2.client import R2_BUCKET
from app.r2.methods import head_object, get_bytes, put_bytes
from app.db.models import DerivedImage
from app.core.config import settings
from app.services.image_assets import (
    IMAGE_STATUS_ACTIVE,
    IMAGE_STATUS_EXPIRED,
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

def _normalize_public_base_url(base_url: str | None) -> str | None:
    if not base_url:
        return None
    return base_url if base_url.endswith("/") else f"{base_url}/"


def _user_public_base_url() -> str:
    return _normalize_public_base_url(Settings.R2_PUBLIC_BASE_URL) or ""


def _openai_public_base_url() -> str:
    return _normalize_public_base_url(Settings.R2_OPENAI_PUBLIC_BASE_URL) or _user_public_base_url()


def _known_public_base_urls() -> tuple[str, ...]:
    known: list[str] = []
    for raw_base_url in (Settings.R2_PUBLIC_BASE_URL, Settings.R2_OPENAI_PUBLIC_BASE_URL):
        normalized = _normalize_public_base_url(raw_base_url)
        if normalized and normalized not in known:
            known.append(normalized)
    return tuple(known)


def _strip_legacy_bucket_prefix(path: str) -> str:
    bucket_prefix = f"{R2_BUCKET}/"
    if path.startswith(bucket_prefix):
        return path[len(bucket_prefix):]
    return path


def _public_url(key: str, *, for_openai: bool = False) -> str:
    base_url = _openai_public_base_url() if for_openai else _user_public_base_url()
    return f"{base_url}{key}"

def _key_from_public_url(url: str) -> Optional[str]:
    for public_base_url in _known_public_base_urls():
        if url.startswith(public_base_url):
            return _strip_legacy_bucket_prefix(url[len(public_base_url):])
    return None  # external URL or a different domain → pass through as-is

def _decide_target(mime: str, has_alpha: bool) -> str:
    if mime in SUPPORTED_DIRECT:
        return "direct"
    # HEIC/HEIF/TIFF/BMP/etc.
    return "png" if has_alpha else "jpeg"

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
        # downscale
        if max(im.size) > max_side:
            im.thumbnail((max_side, max_side))
        has_alpha = (im.mode in ("RGBA", "LA")) or ("transparency" in im.info)
        buf = io.BytesIO()
        if target == "jpeg":
            im = _flatten_alpha_to_rgb(im)
            im.save(buf, format="JPEG", quality=85, optimize=True)
            return buf.getvalue(), "image/jpeg", has_alpha
        elif target == "png":
            im.save(buf, format="PNG", optimize=True)
            return buf.getvalue(), "image/png", has_alpha
        elif target == "webp":
            im.save(buf, format="WEBP", quality=85, method=6)
            return buf.getvalue(), "image/webp", has_alpha
        else:
            raise ValueError(f"Unsupported target: {target}")


def _derive_image_sync(original: bytes, mime: str, max_size: int) -> tuple[bytes, str, str]:
    try:
        with Image.open(io.BytesIO(original)) as im:
            has_alpha = (im.mode in ("RGBA", "LA")) or ("transparency" in im.info)
    except Exception:
        has_alpha = False

    target = _decide_target(mime, has_alpha)
    converted, converted_mime, _ = _transcode(original, target=target, max_side=max_size)
    return converted, converted_mime, target

async def ensure_openai_compatible_image_url(
    session: AsyncSession,
    url_or_key: str,
    *,
    max_size: int = 2048,
) -> str:
    """
    If this is our R2 public URL, ensure it's directly consumable by OpenAI.
    - If already PNG/JPEG/WEBP/GIF → return original public URL.
    - Else (e.g., HEIC) → derive once (JPEG/PNG), cache, and return derived public URL.
    External URLs: returned unchanged.
    """
    asset = await find_asset_by_url(session, url_or_key)
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

    key = _key_from_public_url(url_or_key)
    if key is None:
        # Not our bucket / unknown domain → let OpenAI fetch it as-is
        return url_or_key

    openai_url = _public_url(key, for_openai=True)

    # HEAD → content-type
    meta = await head_object(key)
    mime = (meta.get("ContentType") or "application/octet-stream").lower()

    if mime in SUPPORTED_DIRECT:
        try:
            await wait_for_image_url_reachability(openai_url, logger=logger, require_success=True)
        except ImageReachabilityError as exc:
            raise HTTPException(status_code=409, detail="image_not_ready") from exc
        return openai_url

    # See if we already have a derived variant
    target_guess = "png" if "png" in mime else "jpeg"
    res = await session.exec(
        select(DerivedImage).where(
            DerivedImage.original_key == key,
            DerivedImage.target_format == target_guess,
            DerivedImage.max_side == max_size,
        )
    )
    row = res.first()
    if row:
        derived_openai_url = _public_url(row.derived_key, for_openai=True)
        try:
            await wait_for_image_url_reachability(derived_openai_url, logger=logger, require_success=True)
        except ImageReachabilityError as exc:
            raise HTTPException(status_code=409, detail="image_not_ready") from exc
        return derived_openai_url

    # Pull original bytes, transcode, and store.
    # Offload PIL decode/transcode to a worker thread to avoid blocking the event loop.
    original = await get_bytes(key)
    converted, converted_mime, target = await asyncio.to_thread(
        _derive_image_sync,
        original,
        mime,
        max_size,
    )

    sha = hashlib.sha256(converted).hexdigest()
    ext = ".jpg" if converted_mime == "image/jpeg" else ".png" if converted_mime == "image/png" else ".webp"
    derived_key = f"derived/{sha[:2]}/{sha}{ext}"

    await put_bytes(derived_key, converted, content_type=converted_mime, metadata={"source": "derived"})
    session.add(DerivedImage(
        original_key=key,
        target_format=target,
        max_side=max_size,
        derived_key=derived_key,
    ))
    await session.commit()

    derived_openai_url = _public_url(derived_key, for_openai=True)
    try:
        await wait_for_image_url_reachability(derived_openai_url, logger=logger, require_success=True)
    except ImageReachabilityError as exc:
        raise HTTPException(status_code=409, detail="image_not_ready") from exc

    return derived_openai_url
