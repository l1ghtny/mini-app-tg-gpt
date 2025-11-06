from __future__ import annotations
import io
import hashlib
from typing import Optional, Tuple
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from PIL import Image, ImageOps

from app.r2.settings import Settings
from app.r2.client import R2_BUCKET
from app.r2.methods import head_object, get_bytes, put_bytes
from app.db.models import DerivedImage, MessageContent

# Enable HEIC/HEIF if pillow-heif is installed
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    raise ImportError("pillow-heif is not installed")


SUPPORTED_DIRECT = {"image/png", "image/jpeg", "image/webp", "image/gif"}

def _public_url(key: str) -> str:
    # Your existing pattern: {R2_PUBLIC_BASE_URL}{bucket}/{key}
    return f"{Settings.R2_PUBLIC_BASE_URL}{R2_BUCKET}/{key}"

def _key_from_public_url(url: str) -> Optional[str]:
    base = f"{Settings.R2_PUBLIC_BASE_URL}{R2_BUCKET}/"
    if url.startswith(base):
        return url[len(base):]
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
    im = Image.open(io.BytesIO(data))
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

async def ensure_openai_compatible_image_url(
    session: AsyncSession,
    url_or_key: str,
    *,
    max_side: int = 2048,
) -> str:
    """
    If this is our R2 public URL, ensure it's directly consumable by OpenAI.
    - If already PNG/JPEG/WEBP/GIF → return original public URL.
    - Else (e.g., HEIC) → derive once (JPEG/PNG), cache, and return derived public URL.
    External URLs: returned unchanged.
    """
    key = _key_from_public_url(url_or_key)
    if key is None:
        # Not our bucket / unknown domain → let OpenAI fetch it as-is
        return url_or_key

    # HEAD → content-type
    meta = await head_object(key)
    mime = (meta.get("ContentType") or "application/octet-stream").lower()

    if mime in SUPPORTED_DIRECT:
        return _public_url(key)

    # See if we already have a derived variant
    target_guess = "png" if "png" in mime else "jpeg"
    res = await session.exec(
        select(DerivedImage).where(
            DerivedImage.original_key == key,
            DerivedImage.target_format == target_guess,
            DerivedImage.max_side == max_side,
        )
    )
    row = res.first()
    if row:
        return _public_url(row.derived_key)

    # Pull original bytes, transcode, and store
    original = await get_bytes(key)
    # Detect alpha to pick target better
    try:
        im = Image.open(io.BytesIO(original))
        has_alpha = (im.mode in ("RGBA", "LA")) or ("transparency" in im.info)
    except Exception:
        has_alpha = False
    target = _decide_target(mime, has_alpha)
    converted, converted_mime, _ = _transcode(original, target=target, max_side=max_side)

    sha = hashlib.sha256(converted).hexdigest()
    ext = ".jpg" if converted_mime == "image/jpeg" else ".png" if converted_mime == "image/png" else ".webp"
    derived_key = f"derived/{sha[:2]}/{sha}{ext}"

    await put_bytes(derived_key, converted, content_type=converted_mime, metadata={"source": "derived"})
    session.add(DerivedImage(
        original_key=key,
        target_format=target,
        max_side=max_side,
        derived_key=derived_key,
    ))
    await session.commit()

    return _public_url(derived_key)


async def rewrite_message_image_url(
    session: AsyncSession,
    old_url: str,
    new_url: str,
    message_id: str | None = None,
) -> int:
    """
    Update MessageContent rows where value==old_url to new_url.
    Optionally scope to a single message to keep the write tiny.
    Returns number of rows updated.
    """
    q = select(MessageContent).where(
        MessageContent.type == "image_url",
        MessageContent.value == old_url,
    )
    if message_id:
        q = q.where(MessageContent.message_id == message_id)

    rows = (await session.exec(q)).all()
    for r in rows:
        r.value = new_url
    if rows:
        await session.commit()
    return len(rows)