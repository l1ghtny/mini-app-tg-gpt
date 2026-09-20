"""Admission and estimates share the same server-owned context and policy."""

import hashlib
import hmac
import json
import time
from fastapi import HTTPException
from sqlmodel import select
from app.core.config import settings
from app.db.models import Message, MessageContent
from app.services import allowance
from app.services.allowance_policy import (
    LUNA,
    RATE_VERSION,
    step_budget,
    usage_units,
)


def tool_names(choice):
    if choice == "auto" or choice is None:
        return {"web_search", "file_search", "image_generation"}
    if isinstance(choice, str):
        return {choice} if choice != "none" else set()
    return set(choice)


async def estimate(session, user, conversation, request):
    allowance.require_enabled(user.id)
    a = await allowance.account(session, user.id)
    if request.model not in allowance.model_access(a.plan):
        raise HTTPException(403, detail={"error": "model_not_in_plan"})
    rows = (
        await session.exec(
            select(MessageContent.type, MessageContent.value)
            .join(Message, Message.id == MessageContent.message_id)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(100)
        )
    ).all()
    text = "\n".join(value for kind, value in rows if kind == "text")
    # Bound the retained history, matching the shared-provider summary strategy.
    text = text[-settings.SHARED_ALLOWANCE_HISTORY_TOKENS * 4 :]
    content = [{"type": "input_text", "text": text}] + [
        {"type": "input_image", "image_url": p.value}
        if p.type == "image_url"
        else {"type": "input_text", "text": p.value}
        for p in request.content
    ]
    from app.api.chat_helpers import _build_history_for_openai
    from app.services.allowance_context import context_partition, clean_messages

    history = await _build_history_for_openai(
        session, conversation.id, model_name=request.model
    )
    current = {"role": "user", "content": content[1:]}
    older, recent = context_partition(
        history + [current], settings.SHARED_ALLOWANCE_HISTORY_TOKENS * 4
    )
    messages = clean_messages(recent)
    if older:
        # Reserve the bounded summary's possible size before it is generated.
        messages.insert(
            0,
            {"role": "user", "content": [{"type": "input_text", "text": " " * 12288}]},
        )
    tools = tool_names(request.tool_choice)
    from app.api.chat_helpers import _resolve_system_prompt
    from app.services.openai_service import _instructions_for_openai

    instructions = _instructions_for_openai(
        request.model, _resolve_system_prompt(conversation, user)
    )
    upper = step_budget(request.model, messages, instructions + " " * 512)
    included = request.model == LUNA
    paid_upper = 0 if included else upper
    # Leave room for a follow-up after a tool result, without forcing a tool call.
    if tools:
        paid_upper += (upper if not included else 0) + 150_000
    if "image_generation" in tools:
        paid_upper += 500_000
    ceiling = min(paid_upper, allowance.available(a))
    if request.spend_limit_units is not None:
        ceiling = min(ceiling, request.spend_limit_units)
    lower = (
        0
        if included
        else usage_units(
            request.model,
            {"input_tokens": min(len(text) // 3 + 1024, 10000), "output_tokens": 400},
        )
    )
    typical = (
        0
        if included
        else usage_units(
            request.model,
            {"input_tokens": min(len(text) // 3 + 2048, 14000), "output_tokens": 2000},
        )
    )
    if request.required_tool == "image_generation":
        lower += 30_000
        typical += 100_000
    if request.required_tool == "web_search":
        lower += 10_000
        typical += 30_000
    fingerprint = hashlib.sha256(
        json.dumps(
            dict(
                conversation=str(conversation.id),
                model=request.model,
                content=[p.model_dump() for p in request.content],
                tools=request.tool_choice,
                required=request.required_tool,
                reasoning=request.reasoning_effort,
                thinking=request.thinking,
                image_quality=request.image_quality,
                history=[(k, v) for k, v in rows],
                summary=conversation.history_summary,
                instructions=instructions,
                context_hash=hashlib.sha256(
                    json.dumps(history, sort_keys=True).encode()
                ).hexdigest(),
                rate=RATE_VERSION,
            ),
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    expires = int(time.time()) + 300
    signature = hmac.new(
        (settings.SECRET_KEY or "local").encode(),
        f"{user.id}:{fingerprint}:{expires}".encode(),
        "sha256",
    ).hexdigest()
    token = f"{expires}.{fingerprint}.{signature}"
    if request.estimate_reference:
        try:
            old_exp, old_fp, old_sig = request.estimate_reference.split(".")
            expected = hmac.new(
                (settings.SECRET_KEY or "local").encode(),
                f"{user.id}:{old_fp}:{old_exp}".encode(),
                "sha256",
            ).hexdigest()
            valid = (
                int(old_exp) >= int(time.time())
                and old_fp == fingerprint
                and hmac.compare_digest(old_sig, expected)
            )
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise HTTPException(409, detail={"error": "usage_estimate_stale"})
    result = dict(
        estimate_reference=token,
        expires_at=expires,
        rate_version=RATE_VERSION,
        estimated_min_percent=round(lower * 100 / a.granted, 2),
        estimated_max_percent=round(typical * 100 / a.granted, 2),
        ceiling_units=ceiling,
        minimum_ceiling_units=(0 if included else upper)
        + (500_000 if request.required_tool == "image_generation" else 0),
        ceiling_percent=round(ceiling * 100 / a.granted, 2),
        needs_confirmation=typical * 100 / a.granted >= 5
        or request.required_tool == "image_generation",
        luna_ceiling=(upper * 2 if included else 0) + (50_000 if older else 0),
    )
    await session.commit()
    return result


async def admit(session, user, conversation, request):
    e = await estimate(session, user, conversation, request)
    if e["needs_confirmation"] and not request.estimate_reference:
        raise HTTPException(409, detail={"error": "usage_confirmation_required", **e})
    if e["ceiling_units"] < e["minimum_ceiling_units"]:
        raise HTTPException(
            402,
            detail={
                "error": "request_spend_limit"
                if request.spend_limit_units is not None
                else "allowance_insufficient"
            },
        )
    return await allowance.reserve(
        session,
        user_id=user.id,
        conversation_id=conversation.id,
        request_id=request.client_request_id,
        model=request.model,
        ceiling=e["ceiling_units"],
        luna_ceiling=e["luna_ceiling"],
    )
