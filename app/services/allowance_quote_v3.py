"""Non-consuming customer quotes and separately funded supplier plans."""

import hashlib
import hmac
import json
import time
from dataclasses import replace

from fastapi import HTTPException

from app.core.config import settings
from app.services import allowance
from app.services.allowance_policy import LUNA, RATE_VERSION, step_budget
from app.services.allowance_task_policy import ITERATIVE, limits
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
    image_estimate,
    references=0,
    summary_units=0,
    consent_instructions="",
):
    from app.services.shared_chat_provider import (
        TOOL_DESCRIPTIONS,
        provider_tool_schemas,
        tool_instructions,
        iterative_instructions,
    )

    policy = limits(account)
    selected = set(tools) & TOOL_DESCRIPTIONS.keys()
    if not document_stores:
        selected.discard("file_search")
    choice = request.tool_choice
    auto = (
        choice is None
        or choice == "auto"
        or (isinstance(choice, list) and "auto" in choice)
    )
    explicit_image = request.required_tool == "image_generation" or (
        not auto and "image_generation" in tools
    )
    paid_available = allowance.available(account)
    if paid_available <= 0:
        selected -= {"web_search", "file_search", "image_generation", "inspect_image"}
    image_allowed = "image_generation" in selected
    needs_confirmation = explicit_image or (
        image_allowed and image_estimate >= max(1, account.granted * 5 // 100)
    )
    if request.required_tool:
        if request.required_tool not in TOOL_DESCRIPTIONS:
            raise HTTPException(400, detail={"error": "tool_unavailable"})
        if request.required_tool not in selected:
            raise HTTPException(402, detail={"error": "paid_tool_unavailable"})
    instructions = tool_instructions(
        instructions, request.model, document_stores, request.required_tool
    )
    instructions = iterative_instructions(instructions)
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
    if selected and plan.task_budget is not None:
        plan = replace(
            plan,
            task_budget=max(
                plan.task_budget,
                80_000 if request.model == "claude-fable-5-1" else 64_000,
            ),
        )
    schemas = provider_tool_schemas(
        request.model, {name: {} for name in sorted(selected)}
    )
    direct = step_budget(
        request.model,
        messages,
        instructions,
        max_output=plan.max_output_tokens,
        tool_schemas=schemas,
    )
    included = request.model == LUNA
    luna_available = max(
        0, account.luna_granted - account.luna_spent - account.luna_reserved
    )
    # Old clients echo these non-consuming charge maxima. They are never holds.
    ceiling = paid_available
    if explicit_image:
        ceiling = min(ceiling, 3 * direct + image_reserve + summary_units)
    if request.spend_limit_units is not None:
        ceiling = min(ceiling, request.spend_limit_units)
    minimum = (
        0 if included and not request.required_tool else min(paid_available, 10_000)
    )
    if not included and paid_available <= 0:
        minimum = 1
    luna_minimum = 1 if included or summary_units else 0
    luna_ceiling = luna_available if luna_minimum else 0
    stable = dict(
        conversation=str(conversation.id),
        model=request.model,
        content=[p.model_dump() for p in request.content],
        history=history,
        instructions=consent_instructions,
        tools=request.tool_choice,
        required=request.required_tool,
        thinking=request.thinking,
        conversation_thinking=getattr(conversation, "thinking", None),
        reasoning=request.reasoning_effort,
        length=getattr(request, "response_length", "auto"),
        quality=request.image_quality or conversation.image_quality,
        stores=sorted(document_stores),
        rates=RATE_VERSION,
        policy=ITERATIVE,
        image_permission_policy="enabled-tools-v1",
    )
    fingerprint = hashlib.sha256(
        json.dumps(stable, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()
    secret = (settings.SECRET_KEY or "local").encode()
    expires = int(time.time()) + 300

    def sign(exp, fp, cap, lc):
        return hmac.new(
            secret, f"{user.id}:{exp}:{fp}:{cap}:{lc}".encode(), "sha256"
        ).hexdigest()

    if request.estimate_reference:
        try:
            old_exp, old_fp, old_cap, old_luna, old_sig = (
                request.estimate_reference.split(".")
            )
            valid = (
                int(old_exp) >= int(time.time())
                and old_fp == fingerprint
                and hmac.compare_digest(
                    old_sig, sign(old_exp, old_fp, old_cap, old_luna)
                )
            )
            if not valid or min(int(old_cap), int(old_luna)) < 0:
                raise ValueError("stale")
            ceiling = min(ceiling, int(old_cap))
            luna_ceiling = min(luna_ceiling, int(old_luna))
        except (ValueError, TypeError):
            raise HTTPException(409, detail={"error": "usage_estimate_stale"}) from None
    token = f"{expires}.{fingerprint}.{ceiling}.{luna_ceiling}.{sign(expires, fingerprint, ceiling, luna_ceiling)}"
    # Grace is explicit platform support. A tiny cap cannot increase it.
    supplier = min(
        policy["supplier_max"],
        ceiling + luna_ceiling + policy["grace_units"],
        max(
            500_000,
            3 * direct
            + 300_000
            + summary_units
            + (image_reserve if image_allowed else 0),
        ),
    )
    recovery_tokens = min(
        128_000, max(PROFILES[request.model][1], plan.max_output_tokens + 8_000)
    )
    recovery = min(
        policy["supplier_max"],
        step_budget(
            request.model,
            evidence_messages(
                messages,
                tokens=plan.max_output_tokens
                + policy["tool_executions"] * policy["tool_result_tokens"]
                + 2_000,
            ),
            instructions,
            max_output=recovery_tokens,
            tool_schemas=schemas,
        ),
    )
    saved = dict(
        policy=ITERATIVE,
        generation=plan.dump(),
        tools=sorted(selected),
        tool_rounds=policy["planning_turns"],
        recovery_tokens=recovery_tokens,
        image_consent=image_allowed,
        required_tool=request.required_tool,
        supplier_ceiling=supplier,
        risk_policy=policy,
        final_answer_units=direct,
    )
    indicative = (
        0
        if included
        else step_budget(
            request.model, messages, instructions, max_output=1200, tool_schemas=schemas
        )
    ) + (image_reserve if request.required_tool == "image_generation" else 0)
    result = dict(
        estimate_reference=token,
        expires_at=expires,
        rate_version=RATE_VERSION,
        estimated_min_percent=0,
        estimated_max_percent=round(
            100 * min(ceiling, indicative) / account.granted, 2
        ),
        ceiling_units=ceiling,
        minimum_ceiling_units=minimum,
        ceiling_percent=round(100 * ceiling / account.granted, 2),
        needs_confirmation=needs_confirmation,
        image_quality=(request.image_quality or conversation.image_quality or "medium")
        if image_allowed
        else None,
        image_estimated_percent=round(100 * image_estimate / account.granted, 2)
        if image_allowed
        else None,
        luna_minimum=luna_minimum,
        luna_ceiling=luna_ceiling,
        execution_plan=saved,
        recovery_ceiling_units=recovery,
        generation_profile=plan.profile,
        reasoning_effort=plan.effort,
        max_output_tokens=plan.max_output_tokens,
        task_budget_tokens=plan.task_budget,
        generation_policy_version=ITERATIVE,
    )
    await session.commit()
    return result
