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
    image_budget,
    output_target,
    document_followup_messages,
    MAX_CHAT_TOOL_CALLS,
)


def tool_names(choice):
    if (
        choice == "auto"
        or choice is None
        or (isinstance(choice, list) and "auto" in choice)
    ):
        return {"web_search", "file_search", "image_generation"}
    if isinstance(choice, str):
        return {choice} if choice != "none" else set()
    return set(choice)


async def estimate(session, user, conversation, request):
    allowance.require_enabled(user.id)
    a = await allowance.account(session, user.id)
    allowance.require_active(a)
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
    from app.api.chat_helpers import _build_history_for_openai, _resolve_system_prompt
    from app.services.allowance_context import estimate_context
    from app.services.shared_chat_provider import shared_instructions

    history = await _build_history_for_openai(
        session, conversation.id, model_name=request.model
    )
    current = {
        "role": "user",
        "content": [
            {"type": "input_image", "image_url": p.value}
            if p.type == "image_url"
            else {"type": "input_text", "text": p.value}
            for p in request.content
        ],
    }
    messages, needs_summary, references = estimate_context(
        history + [current], conversation
    )
    tools = tool_names(request.tool_choice)
    document_stores = []
    if "file_search" in tools:
        from app.api.document_helpers import list_conversation_ready_vector_store_ids

        document_stores = await list_conversation_ready_vector_store_ids(
            session, conversation.id, user=user
        )
    if references:
        tools.add("inspect_image")
    instructions = shared_instructions(
        request.model, _resolve_system_prompt(conversation, user)
    )
    effort = request.reasoning_effort or (
        "none" if request.thinking is False else "medium"
    )
    quality = request.image_quality or conversation.image_quality or "medium"
    target = output_target(request.model, effort, request.required_tool)
    upper = step_budget(request.model, messages, instructions, max_output=target)
    minimum = step_budget(request.model, messages, instructions, max_output=256)
    included = request.model == LUNA
    refs = min(
        4,
        sum(kind in {"image", "image_url"} for kind, _ in rows)
        + sum(p.type == "image_url" for p in request.content),
    )
    reference_tokens = refs * 1024
    if request.required_tool == "image_generation" and refs:
        from types import SimpleNamespace
        from app.services.allowance_images import image_files, image_reference_tokens

        # Match the edit endpoint's owned-source decoding and dimensions. Only an
        # explicit generation/edit quote reads files, never ordinary chat quotes.
        sources = [v for k, v in reversed(rows) if k in {"image", "image_url"}]
        sources += [p.value for p in request.content if p.type == "image_url"]
        try:
            files = await image_files(
                [{"type": "input_image", "image_url": url} for url in sources[-4:]],
                SimpleNamespace(user_id=user.id),
            )
            reference_tokens = image_reference_tokens(files)
        except (HTTPException, ValueError):
            # Unavailable historical sources must not block unrelated generation.
            # Editing still checks availability before any image-provider spend.
            reference_tokens = refs * 120 * 120  # 3840px decoder bound.
    image_reserve = image_budget(quality, reference_tokens=reference_tokens)
    # Expected usage and admission use the exact same compacted multimodal context.
    # Do not promise cache hits; unused output/tool capacity is never a charge.
    lower = (
        0
        if included
        else step_budget(request.model, messages, instructions, max_output=256)
    )
    typical = (
        0
        if included
        else step_budget(
            request.model,
            messages,
            instructions,
            max_output=2400 if effort == "high" else 1200,
        )
    )
    paid_upper = 0 if included else upper
    tool_budget = 0
    if request.required_tool == "image_generation":
        tool_budget = image_reserve
    elif request.required_tool == "web_search":
        tool_budget = 40_000
    elif request.required_tool == "file_search":
        tool_budget = 10_000
    if request.required_tool:
        # A short routing response, tool, and final answer, not two max-length answers.
        continuation = 0 if included else minimum
        paid_upper += continuation + tool_budget
        lower += continuation + tool_budget
        typical += continuation + tool_budget
    elif tools:
        # Optional tools use bounded headroom, not a second full-context reservation.
        # Every actual tool/continuation still passes begin_attempt before spending.
        paid_upper += 40_000
    document_followup = 0
    if document_stores:
        # One routing turn, up to two bounded searches, then the final answer.
        # Reserve the extra input and routing output, not another full answer.
        evidence = document_followup_messages(messages)
        document_followup = step_budget(
            request.model, evidence, instructions, max_output=512
        )
        search_budget = 2500 * len(document_stores) * MAX_CHAT_TOOL_CALLS
        if included:
            paid_upper = max(paid_upper, search_budget)
        else:
            document_upper = upper + document_followup + search_budget
            if request.required_tool == "file_search":
                document_upper += step_budget(
                    request.model, [], max_output=output_target(request.model, effort)
                ) - step_budget(request.model, [], max_output=target)
            paid_upper = max(paid_upper, document_upper)
            typical += document_followup + search_budget
    threshold = max(1, a.granted * 5 // 100)
    needs_confirmation = (
        typical >= threshold
        or (not included and minimum >= threshold)
        or request.required_tool == "image_generation"
        or ("image_generation" in tools and image_reserve >= threshold)
    )
    if "image_generation" in tools and image_reserve >= threshold:
        paid_upper = max(
            paid_upper,
            upper + minimum + image_reserve if not included else image_reserve,
        )
    if not needs_confirmation:
        paid_upper = min(paid_upper, threshold)
    ceiling = min(paid_upper, allowance.available(a))
    if request.spend_limit_units is not None:
        ceiling = min(ceiling, request.spend_limit_units)
    minimum_ceiling = (0 if included else minimum) + tool_budget
    if request.required_tool and not included:
        minimum_ceiling += minimum
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
                image_quality=quality,
                history=[(k, v) for k, v in rows],
                summary=conversation.history_summary,
                instructions=instructions,
                context_hash=hashlib.sha256(
                    json.dumps(history, sort_keys=True).encode()
                ).hexdigest(),
                rate=RATE_VERSION,
                context_policy="bounded-documents-v3",
                document_stores=sorted(document_stores),
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
        estimated_min_percent=round(min(lower, ceiling) * 100 / a.granted, 2),
        estimated_max_percent=round(min(typical, ceiling) * 100 / a.granted, 2),
        ceiling_units=ceiling,
        minimum_ceiling_units=minimum_ceiling,
        ceiling_percent=round(ceiling * 100 / a.granted, 2),
        needs_confirmation=needs_confirmation,
        image_quality=quality if "image_generation" in tools else None,
        image_estimated_percent=round(image_reserve * 100 / a.granted, 2)
        if "image_generation" in tools
        else None,
        luna_minimum=minimum if included else 0,
        luna_ceiling=min(
            (max(upper * 2, upper + document_followup) if included else 0)
            + (50_000 if needs_summary else 0),
            max(0, a.luna_granted - a.luna_spent - a.luna_reserved),
        ),
    )
    await session.commit()
    return result


async def admit(session, user, conversation, request):
    e = await estimate(session, user, conversation, request)
    if e["needs_confirmation"] and not request.estimate_reference:
        raise HTTPException(409, detail={"error": "usage_confirmation_required", **e})
    if request.model == LUNA and e["luna_ceiling"] < e["luna_minimum"]:
        raise HTTPException(429, detail={"error": "luna_fair_use"})
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
