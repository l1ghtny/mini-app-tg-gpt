"""Flare remains a chat tool; direct image calls expose its billable usage."""

from decimal import Decimal
from openai import APIStatusError
from app.services.allowance_policy import FLARE, ceil_units


def image_usage_units(usage):
    details = usage.get("input_tokens_details") or {}
    text = int(details.get("text_tokens", 0))
    images = int(details.get("image_tokens", 0))
    output = int(usage.get("output_tokens", 0))
    cached = details.get("cached_tokens_details") or {}
    ct, ci = int(cached.get("text_tokens", 0)), int(cached.get("image_tokens", 0))
    if min(text, images, output, ct, ci) < 0 or ct > text or ci > images:
        raise ValueError("Invalid image usage")
    return ceil_units(
        Decimal(text - ct) * 5
        + Decimal(ct) * Decimal("1.25")
        + Decimal(images - ci) * 8
        + Decimal(ci) * 2
        + Decimal(output) * 30
    )


async def image_files(refs, run):
    from app.db.database import engine
    from sqlmodel.ext.asyncio.session import AsyncSession
    from app.services.background.image_deriver import require_owned_image_asset
    from app.services.image_assets import effective_image_status, IMAGE_STATUS_ACTIVE
    from app.r2.methods import head_object, get_bytes

    files = []
    async with AsyncSession(engine, expire_on_commit=False) as session:
        for i, part in enumerate(refs):
            asset = await require_owned_image_asset(
                session, part["image_url"], user_id=run.user_id
            )
            if effective_image_status(asset) != IMAGE_STATUS_ACTIVE:
                raise ValueError("Reference image is unavailable")
            meta = await head_object(asset.key)
            mime = meta.get("ContentType", "").split(";")[0]
            if mime not in {"image/png", "image/jpeg", "image/webp"}:
                raise ValueError("Unsupported reference image format")
            if int(meta.get("ContentLength", 0)) > 50_000_000:
                raise ValueError("Reference image is too large")
            data = await get_bytes(asset.key)
            if len(data) > 50_000_000:
                raise ValueError("Reference image is too large")
            files.append((f"reference-{i}.{mime.split('/')[-1]}", data, mime))
    return files


async def generate_image(run, query, refs, quality, reference_mode="none"):
    from app.services.openai_service import client

    if reference_mode not in {"none", "latest", "recent"}:
        raise ValueError("Invalid image reference selection")
    if reference_mode == "none":
        refs = []
    elif run.conversation_id:
        from app.db.database import engine
        from app.db.models import Conversation, Message, MessageContent
        from sqlmodel import select
        from sqlmodel.ext.asyncio.session import AsyncSession

        async with AsyncSession(engine, expire_on_commit=False) as session:
            rows = (
                await session.exec(
                    select(MessageContent.value)
                    .join(Message, Message.id == MessageContent.message_id)
                    .join(Conversation, Conversation.id == Message.conversation_id)
                    .where(
                        Conversation.id == run.conversation_id,
                        Conversation.user_id == run.user_id,
                        MessageContent.type.in_(["image", "image_url"]),
                    )
                    .order_by(Message.created_at.desc(), MessageContent.ordinal.desc())
                    .limit(1 if reference_mode == "latest" else 4)
                )
            ).all()
            refs = []
            for url in reversed(rows):
                refs.append({"type": "input_image", "image_url": url})
        if not refs:
            raise ValueError("No available image to edit; attach an image first")
    files = await image_files(refs, run) if refs else []
    # A conservative reservation; actual counters settle the request afterward.
    attempt = await run.start(FLARE, 500_000)
    params = dict(
        model=FLARE,
        prompt=query,
        quality=quality,
        size="1024x1024",
        n=1,
        output_format="png",
    )
    try:
        api = client.with_options(max_retries=0).images
        response = (
            await api.edit(image=files, **params)
            if files
            else await api.generate(**params)
        )
    except APIStatusError as exc:
        if exc.status_code in {400, 401, 403, 404, 422, 429}:
            await run.finish(attempt, FLARE, {}, success=False, units=0)
        raise
    if response.usage is None:
        raise ValueError("Image endpoint omitted usage")
    raw = response.usage.model_dump()
    usage = dict(
        input_tokens=raw["input_tokens"],
        output_tokens=raw["output_tokens"],
        image_usage=raw,
    )
    images = [x.b64_json for x in response.data or [] if x.b64_json]
    await run.finish(
        attempt,
        FLARE,
        usage,
        getattr(response, "_request_id", None),
        success=bool(images),
        units=image_usage_units(raw),
    )
    if not images:
        raise RuntimeError("No image returned")
    return images
