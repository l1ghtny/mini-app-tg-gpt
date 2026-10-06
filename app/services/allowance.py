import logging

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from dateutil.relativedelta import relativedelta
from fastapi import HTTPException
from sqlalchemy import func
from uuid import uuid4
from app.db.subscription_tiers import (
    SubscriptionTier,
    UserSubscription,
    SubscriptionStatus,
)
from sqlmodel import select
from app.core.config import settings
from app.db.models import AppUser
from app.db.allowance import (
    AllowanceAccount,
    AllowanceControl,
    AllowanceRequest,
    AllowanceEvent,
    ProviderAttempt,
)
from app.services.allowance_policy import (
    GRANT_VERSION_V2,
    grant_units,
    PRIVATE_PLANS,
    IMAGE_OUTPUT_TOKENS,
    RATE_VERSION,
    PLANS,
    MODELS,
    model_access,
    public_plans,
)


def enabled(user_id=None) -> bool:
    if not settings.SHARED_ALLOWANCE_ENABLED:
        return False
    if settings.DEPLOYMENT_CHANNEL == "production":
        return user_id is not None and (settings.SHARED_ALLOWANCE_TRIAL_ENABLED or str(user_id).lower() in settings.SHARED_ALLOWANCE_PRIVATE_USER_IDS)
    if settings.DEPLOYMENT_CHANNEL == "beta":
        return (
            user_id is not None
            and str(user_id).lower() in settings.BETA_ALLOWED_USER_IDS
        )
    return settings.DEPLOYMENT_CHANNEL == "local" and (
        settings.DEBUG_MODE or settings.TEST_ENV
    )


def accounting_scope():
    return settings.SHARED_ALLOWANCE_SCOPE or settings.DEPLOYMENT_CHANNEL


@dataclass(frozen=True)
class PrivateAccess:
    plan: str
    tier_name: str
    started_at: datetime
    expires_at: datetime | None


async def private_access(session, user_id, *, now=None):
    """No starter grants, expired subscriptions, or overlapping-tier stacking."""
    rollout_member = str(user_id).lower() in settings.SHARED_ALLOWANCE_PRIVATE_USER_IDS
    if not rollout_member and not settings.SHARED_ALLOWANCE_TRIAL_ENABLED:
        return None
    now = now or datetime.now(UTC).replace(tzinfo=None)
    rows = (await session.exec(
        select(SubscriptionTier, UserSubscription).join(UserSubscription)
        .where(UserSubscription.user_id == user_id,
               UserSubscription.status == SubscriptionStatus.active,
               UserSubscription.started_at <= now,
               (UserSubscription.expires_at.is_(None)) | (UserSubscription.expires_at > now),
               SubscriptionTier.is_active.is_(True),
               SubscriptionTier.name.in_(list(PRIVATE_PLANS)) | SubscriptionTier.allowance_plan_key.in_(("start", "plus", "premium", "max")))
    )).all()
    if not rows:
        if rollout_member:
            raise HTTPException(403, detail={"error": "private_allowance_inactive"})
        return None
    def plan_for(tier):
        return tier.allowance_plan_key if tier.allowance_plan_key in {"start", "plus", "premium", "max"} else PRIVATE_PLANS[tier.name]
    tier, paid_subscription = max(rows, key=lambda pair: (PLANS[plan_for(pair[0])]["multiple"], bool(pair[0].allowance_plan_key), pair[1].started_at if pair[0].allowance_plan_key else datetime.min, pair[0].name))
    if tier.allowance_plan_key:
        return PrivateAccess(plan=tier.allowance_plan_key, tier_name=tier.name,
            started_at=paid_subscription.started_at, expires_at=paid_subscription.expires_at)
    rows = [(t, s) for t, s in rows if not t.allowance_plan_key]
    # Capacity follows the highest tier; overlapping grants share one clock.
    expiry = (
        None
        if any(sub.expires_at is None for _, sub in rows)
        else max(sub.expires_at for _, sub in rows)
    )
    return PrivateAccess(
        PRIVATE_PLANS[tier.name],
        tier.name,
        min(sub.started_at for _, sub in rows),
        expiry,
    )


async def private_entitlement(session, user_id, *, now=None):
    access = await private_access(session, user_id, now=now)
    return (access.plan, access.tier_name) if access else None


def adjust_private_grant(session, row, plan):
    """Replace capacity, never refill spent usage. Retain already committed holds."""
    policy = PLANS[plan]
    version = (
        GRANT_VERSION_V2
        if settings.SHARED_ALLOWANCE_GENERATION_V2_ENABLED
        else row.grant_policy_version
    )
    grant = max(grant_units(plan, version=version), row.spent + row.reserved)
    if row.plan == plan:
        grant = max(grant, row.granted)
    luna = max(policy["luna_units"], row.luna_spent + row.luna_reserved)
    if (row.plan, row.granted, row.luna_granted, row.grant_policy_version) == (plan, grant, luna, version):
        return
    session.add(AllowanceEvent(account_id=row.id, event_key=f"adjust:{row.id}:{uuid4()}",
        kind="plan_adjustment", units=grant-row.granted, luna_units=luna-row.luna_granted))
    row.plan, row.granted, row.luna_granted = plan, grant, luna
    row.grant_policy_version = version
    session.add(row)


def require_enabled(user_id):
    if not enabled(user_id):
        raise HTTPException(404, detail="Shared allowance is not enabled")


def period(now=None):
    """Calendar window for the platform supplier-spend guard and legacy preview."""
    now = now or datetime.now(UTC).replace(tzinfo=None)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return start, end


def subscription_period(anchor, expires_at, now):
    """One grant for a short invitation; anniversary months for ongoing access."""
    if expires_at is not None and expires_at - anchor <= timedelta(days=31):
        return anchor, expires_at
    months = (now.year - anchor.year) * 12 + now.month - anchor.month
    start = anchor + relativedelta(months=months)
    if start > now:
        months -= 1
        start = anchor + relativedelta(months=months)
    # Calculate from the original anchor so January 31 does not drift to the 28th.
    end = anchor + relativedelta(months=months + 1)
    return start, min(end, expires_at) if expires_at else end


async def subscription_account(session, user_id, scope, access, now):
    """Reuse an open cycle across tier changes; align legacy spend in place."""
    rows = (await session.exec(
        select(AllowanceAccount).where(
            AllowanceAccount.user_id == user_id,
            AllowanceAccount.scope == scope,
            AllowanceAccount.plan != "starter",
            AllowanceAccount.period_start <= now,
        ).order_by(AllowanceAccount.period_start.desc())
        .with_for_update().execution_options(populate_existing=True)
    )).all()
    aligned = next((row for row in rows if row.subscription_anchor is not None), None)
    if aligned and aligned.period_end > now:
        return (
            aligned,
            aligned.period_start,
            aligned.period_end,
            aligned.subscription_anchor,
        )
    anchor = access.started_at
    if aligned and access.started_at <= aligned.period_end:
        # An uninterrupted renewal or overlapping tier retains its anniversary.
        anchor = aligned.subscription_anchor
    start, end = subscription_period(anchor, access.expires_at, now)
    # A short fixed-term cycle can have been extended before expiration. Its
    # original allowance must end before the next funded period begins.
    if aligned and start < aligned.period_end <= now:
        start = aligned.period_end
        end = (
            min(start + relativedelta(months=1), access.expires_at)
            if access.expires_at
            else start + relativedelta(months=1)
        )
    legacy = [
        row
        for row in rows
        if row.subscription_anchor is None and row.period_end > start
    ]
    if len(legacy) > 1:
        # Never silently discard or double-grant two pre-cutover balances.
        raise HTTPException(503, detail={"error": "allowance_period_reconciliation_required"})
    if legacy:
        row = legacy[0]
        old_start, old_end = row.period_start, row.period_end
        row.period_start, row.period_end, row.subscription_anchor = start, end, anchor
        session.add(row)
        session.add(AllowanceEvent(
            account_id=row.id,
            event_key=f"period_alignment:{row.id}:{old_start.isoformat()}:{old_end.isoformat()}:{start.isoformat()}:{end.isoformat()}",
            kind="period_alignment", units=0, luna_units=0,
        ))
        return row, start, end, anchor
    return None, start, end, anchor


async def account(session, user_id, *, now=None):
    require_enabled(user_id)
    # All updated writers share this durable gate. A cutover takes an exclusive
    # row lock, waiting for admitted transactions before pausing both channels.
    control = (await session.exec(
        select(AllowanceControl).where(AllowanceControl.id == "subscription_periods")
        .with_for_update(read=True).execution_options(populate_existing=True)
    )).one_or_none()
    if control is None or control.period_mode == "paused":
        raise HTTPException(503, detail={"error": "allowance_maintenance"}, headers={"Retry-After": "30"})
    period_mode = control.period_mode
    # Serializes first-grant creation and all request admissions for a user.
    await session.exec(select(AppUser).where(AppUser.id == user_id).with_for_update())
    now = now or datetime.now(UTC).replace(tzinfo=None)
    start, end = period(now)
    scope = accounting_scope()
    access = await private_access(session, user_id, now=now)
    trial = settings.SHARED_ALLOWANCE_TRIAL_ENABLED and not access
    anchor = None
    if trial:
        # One lifetime grant, shared across environments and calendar months.
        start, end = datetime(1970, 1, 1), datetime(9999, 1, 1)
    account_filter = (
        (AllowanceAccount.plan == "starter")
        if trial
        else (
            (AllowanceAccount.scope == scope) & (AllowanceAccount.period_start == start)
        )
    )
    if access and period_mode == "subscription":
        row, start, end, anchor = await subscription_account(session, user_id, scope, access, now)
    else:
        row = (
            await session.exec(
                select(AllowanceAccount)
                .where(AllowanceAccount.user_id == user_id, account_filter)
                .with_for_update().execution_options(populate_existing=True)
            )
        ).first()
    if row is None:
        plan = (
            access.plan
            if access
            else ("starter" if trial else settings.SHARED_ALLOWANCE_BETA_PLAN)
        )
        if plan not in PLANS:
            raise HTTPException(503, detail="Invalid allowance configuration")
        p = PLANS[plan]
        grant_version = (
            GRANT_VERSION_V2
            if settings.SHARED_ALLOWANCE_GENERATION_V2_ENABLED
            else RATE_VERSION
        )
        if grant_version != GRANT_VERSION_V2:
            # Once granted, the new offer survives a generation-flag rollback.
            migrated = (await session.exec(select(AllowanceAccount.id).where(
                AllowanceAccount.user_id == user_id,
                AllowanceAccount.grant_policy_version == GRANT_VERSION_V2,
                (AllowanceAccount.plan == "starter") if trial else (AllowanceAccount.plan != "starter"),
            ).limit(1))).first()
            if migrated:
                grant_version = GRANT_VERSION_V2
        row = AllowanceAccount(
            user_id=user_id,
            scope=scope,
            period_start=start,
            period_end=end,
            subscription_anchor=anchor,
            plan=plan,
            rate_version=RATE_VERSION,
            grant_policy_version=grant_version,
            granted=grant_units(plan, version=grant_version),
            luna_granted=p["luna_units"],
        )
        session.add(row)
        await session.flush()
        session.add(
            AllowanceEvent(
                account_id=row.id,
                event_key=f"grant:{row.id}",
                kind="grant",
                units=row.granted,
                luna_units=row.luna_granted,
            )
        )
    elif access:
        adjust_private_grant(session, row, access.plan)
    elif settings.SHARED_ALLOWANCE_GENERATION_V2_ENABLED:
        adjust_private_grant(session, row, row.plan)
    return row


async def current_plan(session, user_id):
    """Read the cohort's current grant without creating a billing account."""
    if not enabled(user_id):
        return None
    entitlement = await private_entitlement(session, user_id)
    if entitlement:
        return entitlement[0]
    if settings.SHARED_ALLOWANCE_TRIAL_ENABLED:
        return "starter"
    start, _ = period()
    row = (
        await session.exec(
            select(AllowanceAccount).where(
                AllowanceAccount.user_id == user_id,
                AllowanceAccount.scope == accounting_scope(),
                AllowanceAccount.period_start == start,
            )
        )
    ).first()
    plan = row.plan if row else settings.SHARED_ALLOWANCE_BETA_PLAN
    if plan not in PLANS:
        raise HTTPException(503, detail="Invalid allowance configuration")
    return plan


def trial_expired(a, now=None):
    now = now or datetime.now(UTC).replace(tzinfo=None)
    return (
        a.plan == "starter" and a.trial_started_at is not None and now >= a.period_end
    )


def require_active(a):
    if trial_expired(a):
        raise HTTPException(402, detail={"error": "trial_expired", "expires_at": a.period_end.replace(tzinfo=UTC).isoformat()})


def available(a):
    return 0 if trial_expired(a) else a.granted - a.spent - a.reserved


async def snapshot(session, user_id):
    if not enabled(user_id):
        return {"enabled": False}
    await release_stale_requests(
        session,
        user_id=user_id,
        cutoff=datetime.now(UTC).replace(tzinfo=None)
        - timedelta(seconds=settings.SHARED_ALLOWANCE_REQUEST_SECONDS + 300),
    )
    a = await account(session, user_id)
    access = await private_access(session, user_id)
    requests = (
        await session.exec(
            select(AllowanceRequest)
            .where(AllowanceRequest.account_id == a.id)
            .order_by(AllowanceRequest.created_at.desc())
            .limit(20)
        )
    ).all()
    attempts = (
        (
            await session.exec(
                select(ProviderAttempt).where(
                    ProviderAttempt.request_id.in_([r.id for r in requests])
                )
            )
        ).all()
        if requests
        else []
    )
    activities = {}
    for attempt in attempts:
        kinds = activities.setdefault(attempt.request_id, set())
        if attempt.model == "gpt-image-2.5-flare":
            details = attempt.usage_details or {}
            kinds.add(
                "image_edit"
                if details.get("image_action") == "edit"
                else "image_generation"
            )
        if attempt.search_calls:
            kinds.add("web_search")
        if attempt.file_calls:
            kinds.add("file_search")
    trial = a.plan == "starter"
    access_ends = access.expires_at if access else None
    period_ends = min(a.period_end, access_ends) if access_ends else a.period_end
    ends_with_access = (
        a.subscription_anchor is not None
        and access_ends is not None
        and access_ends <= a.period_end
    )
    luna_percent = (
        100 * max(0, a.luna_granted - a.luna_spent - a.luna_reserved) / a.luna_granted
        if a.luna_granted else 0
    )
    result = dict(
        trial=(dict(
            state="expired" if trial_expired(a) else ("active" if a.trial_started_at else "ready"),
            duration_days=7,
            started_at=a.trial_started_at.replace(tzinfo=UTC).isoformat() if a.trial_started_at else None,
            expires_at=a.period_end.replace(tzinfo=UTC).isoformat() if a.trial_started_at else None,
            luna_remaining_percent=luna_percent,
        ) if trial else None),
        enabled=True,
        document_generation_available=settings.CHAT_DOCUMENT_GENERATION_ENABLED,
        mode="trial" if trial else "shared" if str(user_id).lower() in settings.SHARED_ALLOWANCE_PRIVATE_USER_IDS else "beta",
        tier_name=access.tier_name if access else None,
        plan=a.plan,
        multiple=PLANS[a.plan]["multiple"],
        granted_units=a.granted,
        consumed_units=a.spent,
        reserved_units=a.reserved,
        available_units=available(a),
        remaining_percent=round(100 * available(a) / a.granted, 2) if a.granted else 0,
        resets_at=None if trial or ends_with_access else a.period_end.replace(tzinfo=UTC).isoformat(),
        period_ends_at=None if trial and not a.trial_started_at else period_ends.replace(tzinfo=UTC).isoformat(),
        period_end_kind="trial_expiry" if trial else "access_expiry" if ends_with_access else "reset",
        access_expires_at=access_ends.replace(tzinfo=UTC).isoformat() if access_ends else None,
        rate_version=a.rate_version,
        grant_policy_version=a.grant_policy_version,
        models=model_access(a.plan),
        default_model="claude-sonnet-5" if trial else "gpt-5.6-terra",
        image_model="gpt-image-2.5-flare",
        luna_remaining_percent=luna_percent,
        luna_available=not trial_expired(a) and a.luna_granted - a.luna_spent - a.luna_reserved > 0,
        image_output_units={k: v * 30 for k, v in IMAGE_OUTPUT_TOKENS.items()},
        catalog=catalog(),
        plans=public_plans(version=a.grant_policy_version),
        history=[
            dict(
                request_id=r.request_id,
                model=r.model,
                status=r.status,
                activities=sorted(activities.get(r.id, set())),
                percent=round(100 * r.charged / a.granted, 3),
                created_at=r.created_at.replace(tzinfo=UTC).isoformat(),
            )
            for r in requests
        ],
    )
    await session.commit()
    return result


async def reserve(
    session, *, user_id, conversation_id, request_id, model, ceiling, luna_ceiling=0,
    execution_plan=None, recovery_ceiling=0, customer_quote=None,
):
    from app.services import allowance_tasks
    from app.services.allowance_task_policy import ITERATIVE, NO_HOLD

    await allowance_tasks.lock(session)
    a = await account(session, user_id)
    require_active(a)
    old = (
        await session.exec(
            select(AllowanceRequest).where(
                AllowanceRequest.user_id == user_id,
                AllowanceRequest.request_id == request_id,
            )
        )
    ).first()
    if old:
        raise HTTPException(
            409, detail={"error": "request_already_reserved", "request_id": request_id}
        )
    no_hold = (execution_plan or {}).get("policy") == ITERATIVE
    count, managed_active = await allowance_tasks.slots(session, user_id)
    if count >= 2 and (no_hold or managed_active or settings.SHARED_ALLOWANCE_GENERATION_V2_ENABLED):
        raise HTTPException(429, detail={"error": "active_task_limit", "limit": 2})
    if model not in model_access(a.plan):
        raise HTTPException(403, detail={"error": "model_not_in_plan", "model": model})
    if a.rate_version != RATE_VERSION:
        raise HTTPException(503, detail="Allowance rate version unavailable")
    if no_hold:
        if ceiling < 0 or luna_ceiling < 0:
            raise HTTPException(402, detail={"error": "request_spend_limit"})
        ceiling = min(ceiling, available(a))
        luna_ceiling = min(
            luna_ceiling, max(0, a.luna_granted - a.luna_spent - a.luna_reserved)
        )
        if ceiling == 0 and (execution_plan.get("required_tool") or execution_plan.get("image_consent")):
            raise HTTPException(402, detail={"error": "paid_tool_unavailable"})
        if ceiling == 0:
            execution_plan = {**execution_plan, "tools": []}
        if (model != "gpt-5.6-luna" and ceiling == 0) or (
            model == "gpt-5.6-luna" and luna_ceiling == 0
        ):
            raise HTTPException(402, detail={"error": "allowance_insufficient"})
    if ceiling < 0 or ceiling > available(a):
        raise HTTPException(
            402,
            detail={
                "error": "allowance_insufficient",
                "available_units": available(a),
                "required_units": ceiling,
            },
        )
    if luna_ceiling > a.luna_granted - a.luna_spent - a.luna_reserved:
        raise HTTPException(
            429,
            detail={"error": "luna_fair_use", "resets_at": a.period_end.isoformat()},
        )
    r = AllowanceRequest(
        account_id=a.id,
        user_id=user_id,
        scope=a.scope,
        request_id=request_id,
        conversation_id=conversation_id,
        model=model,
        ceiling=ceiling,
        luna_ceiling=luna_ceiling,
        execution_plan=execution_plan,
        recovery_ceiling=recovery_ceiling,
        customer_quote=customer_quote,
    )
    if no_hold:
        policy = execution_plan["risk_policy"]
        r.admission_policy = NO_HOLD
        r.risk_policy = policy
        r.supplier_ceiling = execution_plan["supplier_ceiling"]
        r.admission_period_start = a.period_start
        r.task_deadline_at = allowance_tasks.now() + timedelta(
            seconds=policy["task_seconds"]
        )
        r.lease_expires_at = min(
            r.task_deadline_at,
            allowance_tasks.now() + timedelta(seconds=policy["lease_seconds"]),
        )
        r.execution_state = {"planning_turns": 0, "tools": 0, "images": 0}
    session.add(r)
    await session.flush()
    if not no_hold:
        a.reserved += ceiling
        a.luna_reserved += luna_ceiling
    session.add(a)
    session.add(
        AllowanceEvent(
            account_id=a.id,
            request_id=r.id,
            event_key=f"reserve:{r.id}",
            kind="admit" if no_hold else "reserve",
            units=0 if no_hold else ceiling,
            luna_units=0 if no_hold else luna_ceiling,
        )
    )
    await session.commit()
    return r


async def request_row(session, user_id, request_id, lock=False):
    q = (
        select(AllowanceRequest)
        .join(AllowanceAccount, AllowanceAccount.id == AllowanceRequest.account_id)
        .where(
            AllowanceRequest.user_id == user_id,
            (AllowanceRequest.scope == accounting_scope())
            | (AllowanceAccount.plan == "starter")
            | (AllowanceRequest.admission_policy == "no-hold-v3"),
            AllowanceRequest.request_id == request_id,
        )
    )
    if lock:
        q = q.with_for_update(of=AllowanceRequest).execution_options(populate_existing=True)
    return (await session.exec(q)).first()


async def remaining_request_budget(session, user_id, request_id, *, included=False, recovery=False):
    r = await request_row(session, user_id, request_id)
    if not r or r.status != "reserved":
        raise HTTPException(409, detail="Allowance reservation is not active")
    attempts = (
        await session.exec(
            select(ProviderAttempt).where(
                ProviderAttempt.request_id == r.id,
                (ProviderAttempt.included == included)
                | (r.admission_policy == "no-hold-v3"),
                ProviderAttempt.recovery == recovery,
            )
        )
    ).all()
    used = sum(
        p.supplier_units if p.supplier_units is not None else p.budget for p in attempts
    )
    cap = (
        r.recovery_ceiling
        if recovery
        else r.supplier_ceiling
        if r.admission_policy == "no-hold-v3"
        else r.luna_ceiling
        if included
        else r.ceiling
    )
    return max(0, cap - used)


async def begin_attempt(
    session,
    *,
    user_id,
    request_id,
    step_key,
    model,
    budget,
    included=False,
    recovery=False,
    owner=None,
    protected=0,
    commit=True,
):
    # Serialize the operational budget across users in PostgreSQL.
    if session.bind.dialect.name == "postgresql":
        await session.execute(select(func.pg_advisory_xact_lock(73020920)))
    r = await request_row(session, user_id, request_id, True)
    if not r or r.status != "reserved":
        raise HTTPException(409, detail="Allowance reservation is not active")
    no_hold = r.admission_policy == "no-hold-v3"
    if no_hold:
        from app.services import allowance_tasks

        allowance_tasks.require_owner(r, owner)
    attempts = (
        await session.exec(
            select(ProviderAttempt).where(ProviderAttempt.request_id == r.id)
        )
    ).all()
    if any(p.step_key == step_key for p in attempts):
        raise HTTPException(
            409, detail="Provider step already started; reconcile before retry"
        )
    if recovery and (not r.execution_plan or not r.recovery_ceiling or any(p.recovery for p in attempts)):
        raise HTTPException(409, detail={"error": "recovery_not_available"})
    if recovery and any(p.supplier_units is None for p in attempts):
        raise HTTPException(409, detail={"error": "recovery_not_available"})
    if recovery and not any(
        p.status == "failed"
        and p.supplier_units is not None
        and (p.usage_details or {}).get(
            "incomplete_reason", (p.usage_details or {}).get("stop_reason")
        )
        in {"max_tokens", "max_output_tokens"}
        for p in attempts
    ):
        raise HTTPException(409, detail={"error": "recovery_not_available"})
    used = sum(
        (p.supplier_units if p.supplier_units is not None else p.budget)
        for p in attempts
        if (no_hold or p.included == included) and p.recovery == recovery
    )
    cap = (
        r.recovery_ceiling
        if recovery
        else r.supplier_ceiling
        if no_hold
        else r.luna_ceiling
        if included
        else r.ceiling
    )
    if budget < 0 or protected < 0 or used + budget + protected > cap:
        raise HTTPException(
            402,
            detail={
                "error": "request_spend_limit",
                "required_units": used + budget,
                "ceiling_units": cap,
            },
        )
    if no_hold:
        await allowance_tasks.admit_exposure(session, r, budget, protected)
        r.execution_state = {**(r.execution_state or {}), "final_protection": protected}
        r.lease_expires_at = min(
            r.task_deadline_at,
            allowance_tasks.now() + timedelta(seconds=r.risk_policy["lease_seconds"]),
        )
        session.add(r)
    else:
        from app.services import allowance_tasks
        managed = (await session.exec(select(AllowanceRequest.id).where(
            AllowanceRequest.admission_policy == "no-hold-v3",
            AllowanceRequest.status == "reserved",
            AllowanceRequest.lease_expires_at > allowance_tasks.now()))).first()
        if managed:
            await allowance_tasks.admit_exposure(session, r, budget)
    start, _ = period()
    total = (
        await session.exec(
            select(
                func.coalesce(
                    func.sum(
                        func.coalesce(
                            ProviderAttempt.supplier_units, ProviderAttempt.budget
                        )
                    ),
                    0,
                )
            )
            .join(AllowanceRequest, AllowanceRequest.id == ProviderAttempt.request_id)
            .where(
                AllowanceRequest.scope == r.scope, ProviderAttempt.created_at >= start
            )
        )
    ).one()
    if (
        not no_hold
        and int(total) + budget > settings.SHARED_ALLOWANCE_PROVIDER_BUDGET_UNITS
    ):
        raise HTTPException(503, detail={"error": "beta_spend_paused"})
    if r.execution_plan and not no_hold:
        # Losses are separate from customer balances. Unknown usage remains exposure.
        loss = (
            await session.exec(
                select(
                    func.coalesce(
                        func.sum(
                            func.coalesce(
                                ProviderAttempt.supplier_units, ProviderAttempt.budget
                            )
                        ),
                        0,
                    )
                )
                .join(
                    AllowanceRequest, AllowanceRequest.id == ProviderAttempt.request_id
                )
                .where(
                    AllowanceRequest.scope == r.scope,
                    ProviderAttempt.created_at >= start,
                    (ProviderAttempt.status == "failed")
                    | (AllowanceRequest.status.in_(["failed", "pending", "reserved"]))
                    | ProviderAttempt.recovery.is_(True),
                )
            )
        ).one()
        loss_cap = (
            settings.SHARED_ALLOWANCE_PROVIDER_BUDGET_UNITS
            * max(0, min(100, settings.SHARED_ALLOWANCE_RECOVERY_BUDGET_PERCENT))
            // 100
        )
        if int(loss) + budget > loss_cap:
            raise HTTPException(503, detail={"error": "provider_failure_spend_paused"})
    p = ProviderAttempt(
        request_id=r.id,
        step_key=step_key,
        model=model,
        budget=budget,
        included=included,
        recovery=recovery,
    )
    session.add(p)
    await session.flush()
    if commit:
        await session.commit()
    return p.id


async def finish_attempt(
    session, attempt_id, *, units, usage, provider_id=None, success=True
):
    p = (
        await session.exec(
            select(ProviderAttempt)
            .where(ProviderAttempt.id == attempt_id)
            .with_for_update().execution_options(populate_existing=True)
        )
    ).one()
    if p.status in {"complete", "failed"}:
        return
    p.supplier_units = max(0, int(units))
    if p.supplier_units > p.budget:
        logging.getLogger(__name__).warning(
            "allowance_attempt_over_budget model=%s budget=%s actual=%s",
            p.model, p.budget, p.supplier_units,
        )
    p.customer_units = p.supplier_units if success else 0
    p.status = "complete" if success else "failed"
    p.provider_id = provider_id
    p.usage_details = usage
    p.completed_at = datetime.now(UTC).replace(tzinfo=None)
    for key in (
        "input_tokens",
        "cached_tokens",
        "cache_write_tokens",
        "output_tokens",
        "reasoning_tokens",
        "search_calls",
        "file_calls",
    ):
        setattr(p, key, int(usage.get(key, 0) or 0))
    session.add(p)
    await session.commit()


async def identify_attempt(session, attempt_id, provider_id):
    if not provider_id:
        return
    attempt = await session.get(ProviderAttempt, attempt_id, with_for_update=True)
    if attempt.provider_id and attempt.provider_id != provider_id:
        raise ValueError("Provider identity changed for one attempt")
    attempt.provider_id = provider_id
    session.add(attempt)
    await session.commit()


async def settle(
    session, user_id, request_id, *, success, release_unknown=False, commit=True
):
    from app.services import allowance_tasks

    await allowance_tasks.lock(session)
    r = await request_row(session, user_id, request_id, True)
    if not r or r.status in {"complete", "failed"}:
        return
    a = (
        await session.exec(
            select(AllowanceAccount)
            .where(AllowanceAccount.id == r.account_id)
            .with_for_update().execution_options(populate_existing=True)
        )
    ).one()
    attempts = (
        await session.exec(
            select(ProviderAttempt).where(ProviderAttempt.request_id == r.id)
        )
    ).all()
    no_hold = r.admission_policy == "no-hold-v3"
    unknown = any(p.supplier_units is None for p in attempts)
    if (
        no_hold
        and r.status == "reserved"
        and not allowance_tasks.active(r, allowance_tasks.now())
    ):
        success, release_unknown = False, True
    if (
        success
        and not release_unknown
        and (not unknown or not no_hold)
        and a.plan == "starter"
        and a.trial_started_at is None
    ):
        # Account row lock makes simultaneous successes activate exactly once.
        a.trial_started_at = datetime.now(UTC).replace(tzinfo=None)
        a.period_end = a.trial_started_at + timedelta(days=7)
        session.add(a)
        session.add(
            AllowanceEvent(
                account_id=a.id,
                request_id=r.id,
                event_key=f"trial_start:{a.id}",
                kind="trial_start",
                units=0,
                luna_units=0,
            )
        )
    if unknown and not release_unknown:
        r.status = "pending"
        r.task_owner = None
        r.lease_expires_at = None
        session.add(r)
        if commit:
            await session.commit()
        return
    # A failed logical response is funded by us, including any successful child work.
    if release_unknown and success:
        raise ValueError(
            "Unknown supplier usage can only be released as a platform-funded failure"
        )
    charged = (
        min(r.ceiling, sum(p.customer_units for p in attempts if not p.included))
        if success
        else 0
    )
    luna = min(
        r.luna_ceiling,
        sum(
            p.supplier_units if p.supplier_units is not None else p.budget
            for p in attempts
            if p.included
        ),
    )
    if not success:
        luna = 0
    if no_hold:
        same_period = r.admission_period_start == a.period_start
        charged = min(charged, available(a)) if same_period else 0
        luna = (
            min(
                r.luna_ceiling,
                sum(p.customer_units for p in attempts if p.included),
                max(0, a.luna_granted - a.luna_spent - a.luna_reserved),
            )
            if success and same_period
            else 0
        )
        r.task_owner = None
        r.lease_expires_at = None
    else:
        a.reserved -= r.ceiling
        a.luna_reserved -= r.luna_ceiling
    a.spent += charged
    a.luna_spent += luna
    r.charged = charged
    r.luna_charged = luna
    r.status = "complete" if success else "failed"
    session.add_all(
        [
            a,
            r,
            AllowanceEvent(
                account_id=a.id,
                request_id=r.id,
                event_key=f"settle:{r.id}",
                kind="settle",
                units=charged,
                luna_units=luna,
            ),
        ]
    )
    await session.flush()
    if commit:
        await session.commit()


async def release_stale_requests(session, *, cutoff, user_id=None):
    """Release abandoned customer holds; unknown supplier exposure stays in the spend guard."""
    query = select(AllowanceRequest).where(
        (AllowanceRequest.scope == accounting_scope())
        | (AllowanceRequest.admission_policy == "no-hold-v3"),
        AllowanceRequest.status.in_(["reserved", "pending"]),
        (AllowanceRequest.created_at < cutoff)
        | (
            (AllowanceRequest.admission_policy == "no-hold-v3")
            & (AllowanceRequest.status == "reserved")
            & (
                AllowanceRequest.lease_expires_at
                <= datetime.now(UTC).replace(tzinfo=None)
            )
        ),
    )
    if user_id is not None:
        query = query.where(AllowanceRequest.user_id == user_id)
    rows = (await session.exec(query)).all()
    for row in rows:
        if (
            row.admission_policy == "no-hold-v3"
            and row.lease_expires_at
            and row.lease_expires_at > datetime.now(UTC).replace(tzinfo=None)
        ):
            continue
        await settle(
            session, row.user_id, row.request_id, success=False, release_unknown=True
        )
    return len(rows)


def catalog():
    from app.services.model_availability import text_model_available
    labels = {
        "gpt-5.6-luna": "Luna",
        "gpt-5.6-terra": "Terra",
        "gpt-5.6-sol": "Sol",
        "gpt-6-astra": "Astra",
        "claude-sonnet-5": "Claude Sonnet 5",
        "claude-opus-5": "Claude Opus 5",
        "claude-fable-5-1": "Claude Fable 5.1",
    }
    descriptions = {
        "gpt-5.6-luna": (
            "Everyday questions and quick rewrites",
            "Повседневные вопросы и быстрые правки",
        ),
        "gpt-5.6-terra": (
            "Writing, study and everyday reasoning",
            "Тексты, учёба и повседневные задачи",
        ),
        "gpt-5.6-sol": (
            "Deeper analysis and complex reasoning",
            "Глубокий анализ и сложные рассуждения",
        ),
        "gpt-6-astra": (
            "Demanding analysis and difficult problems",
            "Подробный анализ и трудные задачи",
        ),
        "claude-sonnet-5": ("Writing, research and code", "Тексты, исследования и код"),
        "claude-opus-5": (
            "Complex writing, analysis and code",
            "Сложные тексты, анализ и код",
        ),
        "claude-fable-5-1": (
            "Demanding research and complex projects",
            "Глубокие исследования и сложные проекты",
        ),
    }
    return dict(
        text_models=[
            dict(
                model_name=name,
                available=text_model_available(name, provider=p.provider),
                display_name=labels[name],
                display_name_ru=labels[name],
                provider=p.provider,
                tagline=p.group.title(),
                tagline_ru={
                    "everyday": "На каждый день",
                    "standard": "Для работы и учёбы",
                    "advanced": "Для сложных задач",
                    "flagship": "Максимальные возможности",
                }[p.group],
                best_for=[],
                best_for_ru=[],
                not_great_for=[],
                not_great_for_ru=[],
                badges=[],
                group=p.group,
                intelligence=None,
                description=descriptions[name][0],
                description_ru=descriptions[name][1],
                tier_required=None,
                supports=dict(
                    vision=True,
                    web_search=True,
                    file_search=True,
                    image_gen=True,
                    reasoning=True,
                    thinking=name != "claude-fable-5-1",
                ),
            )
            for name, p in MODELS.items()
        ],
        image_models=[
            dict(
                model_name="gpt-image-2.5-flare",
                display_name="Flare",
                display_name_ru="Flare",
                description="Create images or edit images from this chat. Higher quality uses more of your shared allowance.",
                description_ru="Создавайте изображения и редактируйте картинки из этого чата. Чем выше качество, тем больше расход общего лимита.",
                provider="openai",
                best_for=[],
                best_for_ru=[],
                badges=[],
                tier_required=None,
                qualities=[dict(quality=q) for q in ("low", "medium", "high")],
            )
        ],
        provider_defaults={
            "openai": {"text": "gpt-5.6-terra", "image": "gpt-image-2.5-flare"},
            "anthropic": {"text": "claude-sonnet-5", "image": "gpt-image-2.5-flare"},
        },
        updated_at="2026-09-20T00:00:00Z",
    )
