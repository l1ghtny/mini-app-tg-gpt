"""Price the complete funded workflow, without invoking a generation model."""

import hashlib
import hmac
import json
import time

from fastapi import HTTPException

from app.core.config import settings
from app.services import allowance
from app.services.allowance_policy import (
    LUNA,
    RATE_VERSION,
    MAX_TOOL_QUERY_BYTES,
    image_budget,
    step_budget,
)
from app.services.generation_budget import PROFILES, evidence_messages, execution_plan


async def estimate_plan(
    session,
    user,
    conversation,
    request,
    *,
    account,
    messages,
    instructions,
    tools,
    document_stores,
    image_reserve,
    history,
    references=0,
    summary_units=0,
):
    from app.services.shared_chat_provider import (
        TOOL_DESCRIPTIONS,
        provider_tool_schemas,
        tool_instructions,
    )

    selected = set(tools) & TOOL_DESCRIPTIONS.keys()
    if not document_stores:
        selected.discard("file_search")
    if request.required_tool:
        if request.required_tool not in TOOL_DESCRIPTIONS:
            raise HTTPException(400, detail={"error": "tool_unavailable"})
        selected.add(request.required_tool)
    instructions = tool_instructions(
        instructions, request.model, document_stores, request.required_tool
    )
    try:
        plan = execution_plan(
            request.model,
            messages,
            instructions=instructions,
            reasoning_effort=request.reasoning_effort,
            thinking_enabled=request.thinking
            if request.thinking is not None
            else getattr(conversation, "thinking", None),
            response_length=getattr(request, "response_length", "auto"),
            tool_work=bool(request.required_tool),
        )
    except ValueError as exc:
        raise HTTPException(
            400, detail={"error": "reasoning_effort_not_supported_for_model"}
        ) from exc
    schemas = provider_tool_schemas(
        request.model, {name: {} for name in sorted(selected)}
    )
    # Two tools in one planning round, then an answer-only call. Optional tool
    # routing may itself be the final answer, so it needs the full normal cap.
    first_cap = plan.routing_tokens if request.required_tool else plan.max_output_tokens
    first = step_budget(
        request.model,
        messages,
        instructions,
        max_output=first_cap,
        tool_schemas=schemas,
    )
    followup = evidence_messages(messages, tokens=first_cap + 8_000)
    final = (
        step_budget(
            request.model,
            followup,
            instructions,
            max_output=plan.max_output_tokens,
            tool_schemas=schemas,
        )
        if selected
        else 0
    )
    costs = []
    if "web_search" in selected:
        costs.append(40_000)
    if "file_search" in selected:
        costs.append(2500 * len(document_stores))
    if "inspect_image" in selected:
        costs.append(40_000)
    if "image_generation" in selected:
        # Auto can edit up to four decoded 3840px references, not just thumbnails.
        costs.append(
            max(
                image_reserve,
                image_budget(
                    request.image_quality or conversation.image_quality or "medium",
                    prompt_bytes=MAX_TOOL_QUERY_BYTES,
                    reference_tokens=min(4, references) * 120 * 120,
                ),
            )
        )
    tool_units = 2 * max(costs, default=0)
    included = request.model == LUNA
    paid = tool_units if included else first + final + tool_units
    luna = (first + final if included else 0) + summary_units
    ceiling = min(paid, allowance.available(account))
    if request.spend_limit_units is not None:
        ceiling = min(ceiling, request.spend_limit_units)
    luna_available = max(
        0, account.luna_granted - account.luna_spent - account.luna_reserved
    )
    recovery_tokens = min(
        128_000, max(PROFILES[request.model][1], plan.max_output_tokens + 8_000)
    )
    recovery_units = step_budget(
        request.model,
        evidence_messages(followup, tokens=plan.max_output_tokens + 2_000),
        instructions,
        max_output=recovery_tokens,
        tool_schemas=schemas,
    )
    saved = dict(
        generation=plan.dump(),
        final_answer_units=final,
        tool_budget_units=tool_units,
        tools=sorted(selected),
        tool_rounds=1,
        recovery_tokens=recovery_tokens,
    )
    fingerprint = hashlib.sha256(
        json.dumps(
            dict(
                conversation=str(conversation.id),
                model=request.model,
                content=[p.model_dump() for p in request.content],
                history=history,
                instructions=instructions,
                thinking=request.thinking,
                plan=saved,
                required=request.required_tool,
                quality=request.image_quality or conversation.image_quality,
                stores=sorted(document_stores),
                rates=RATE_VERSION,
                ceiling=ceiling,
                luna_ceiling=min(luna, luna_available),
            ),
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        ).encode()
    ).hexdigest()
    expires = int(time.time()) + 300
    secret = (settings.SECRET_KEY or "local").encode()
    signature = hmac.new(
        secret, f"{user.id}:{fingerprint}:{expires}".encode(), "sha256"
    ).hexdigest()
    token = f"{expires}.{fingerprint}.{signature}"
    if request.estimate_reference:
        try:
            old_exp, old_fp, old_sig = request.estimate_reference.split(".")
            expected = hmac.new(
                secret, f"{user.id}:{old_fp}:{old_exp}".encode(), "sha256"
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
    threshold = max(1, account.granted * 5 // 100)
    # Unlike legacy 256-token admission, the entire selected profile must fit.
    result = dict(
        estimate_reference=token,
        expires_at=expires,
        rate_version=RATE_VERSION,
        estimated_min_percent=0,
        estimated_max_percent=round(100 * ceiling / account.granted, 2),
        ceiling_units=ceiling,
        minimum_ceiling_units=paid,
        ceiling_percent=round(100 * ceiling / account.granted, 2),
        needs_confirmation=ceiling >= threshold
        or request.required_tool == "image_generation",
        image_quality=(request.image_quality or conversation.image_quality or "medium")
        if "image_generation" in selected
        else None,
        image_estimated_percent=round(100 * image_reserve / account.granted, 2)
        if "image_generation" in selected
        else None,
        luna_minimum=luna,
        luna_ceiling=min(luna, luna_available),
        execution_plan=saved,
        recovery_ceiling_units=recovery_units,
        generation_profile=plan.profile,
        reasoning_effort=plan.effort,
        max_output_tokens=plan.max_output_tokens,
        task_budget_tokens=plan.task_budget,
        generation_policy_version=plan.version,
    )
    await session.commit()
    return result
