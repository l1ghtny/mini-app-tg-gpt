"""Database-owned task leases and supplier exposure, independent of balance holds."""

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func
from sqlmodel import select

from app.core.config import settings
from app.db.allowance import AllowanceRequest, ProviderAttempt
from app.services.allowance_task_policy import NO_HOLD


def now():
    return datetime.now(UTC).replace(tzinfo=None)


async def lock(session):
    if session.bind.dialect.name == "postgresql":
        await session.execute(select(func.pg_advisory_xact_lock(73020920)))


def active(row, at):
    if row.status != "reserved":
        return False
    if row.admission_policy == NO_HOLD:
        return (
            row.lease_expires_at is not None
            and row.lease_expires_at > at
            and row.task_deadline_at > at
        )
    return row.created_at > at - timedelta(
        seconds=settings.SHARED_ALLOWANCE_REQUEST_SECONDS
    )


async def slots(session, user_id):
    from app.services import allowance

    at = now()
    rows = (
        await session.exec(
            select(AllowanceRequest)
            .where(
                AllowanceRequest.user_id == user_id,
                AllowanceRequest.status == "reserved",
            )
            .with_for_update()
        )
    ).all()
    for r in rows:
        if r.admission_policy == NO_HOLD and not active(r, at):
            # Never lose unknown cost; only its customer task/slot is closed.
            await allowance.settle(
                session,
                user_id,
                r.request_id,
                success=False,
                release_unknown=True,
                commit=False,
            )
    return (
        sum(active(r, at) for r in rows),
        any(r.admission_policy == NO_HOLD and active(r, at) for r in rows),
    )


def require_owner(row, owner):
    if not owner or row.task_owner != owner or not active(row, now()):
        raise HTTPException(409, detail={"error": "generation_task_expired"})


async def claim(session, user_id, request_id, owner):
    from app.services import allowance

    await lock(session)
    row = await allowance.request_row(session, user_id, request_id, True)
    if not row or not active(row, now()) or row.task_owner is not None:
        raise HTTPException(409, detail={"error": "generation_task_expired"})
    row.task_owner = owner
    row.lease_expires_at = min(
        row.task_deadline_at,
        now() + timedelta(seconds=row.risk_policy["lease_seconds"]),
    )
    session.add(row)
    await session.commit()
    return row


async def state(session, user_id, request_id, owner, updates):
    from app.services import allowance

    await lock(session)
    row = await allowance.request_row(session, user_id, request_id, True)
    require_owner(row, owner)
    row.execution_state = {**(row.execution_state or {}), **updates}
    session.add(row)
    await session.commit()


async def exposure(session, *, user_id=None, days=None):
    """Known net loss plus all unresolved costs; no customer debit is presumed."""
    at = now()
    start = (
        at - timedelta(days=days)
        if days
        else at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    )
    q = (
        select(AllowanceRequest, ProviderAttempt)
        .join(ProviderAttempt, ProviderAttempt.request_id == AllowanceRequest.id)
        .where(
            (ProviderAttempt.created_at >= start)
            | ProviderAttempt.supplier_units.is_(None)
        )
    )
    if user_id is not None:
        q = q.where(AllowanceRequest.user_id == user_id)
    rows = (await session.exec(q)).all()
    costs = {}
    requests = {}
    recent = {}
    for r, p in rows:
        costs[r.id] = costs.get(r.id, 0) + (
            p.supplier_units if p.supplier_units is not None else p.budget
        )
        requests[r.id] = r
        key = (r.id, p.included)
        recent[key] = recent.get(key, 0) + p.customer_units
    all_success = {}
    if requests:
        totals = (
            await session.exec(
                select(
                    ProviderAttempt.request_id,
                    ProviderAttempt.included,
                    func.sum(ProviderAttempt.customer_units),
                )
                .where(ProviderAttempt.request_id.in_(list(requests)))
                .group_by(ProviderAttempt.request_id, ProviderAttempt.included)
            )
        ).all()
        all_success = {(rid, included): int(units) for rid, included, units in totals}
    supplier = sum(costs.values())
    loss = sum(
        max(
            0,
            cost
            - sum(
                credit
                * recent.get((rid, included), 0)
                // max(1, all_success.get((rid, included), 0))
                for included, credit in (
                    (False, requests[rid].charged),
                    (True, requests[rid].luna_charged),
                )
            ),
        )
        for rid, cost in costs.items()
    )
    return supplier, loss


async def admit_exposure(session, row, budget, protected=0):
    active_rows = (
        await session.exec(
            select(AllowanceRequest).where(
                AllowanceRequest.admission_policy == NO_HOLD,
                AllowanceRequest.status == "reserved",
                AllowanceRequest.id != row.id,
            )
        )
    ).all()
    others = sum(
        (r.execution_state or {}).get("final_protection", 0)
        for r in active_rows
        if active(r, now())
    )
    own_others = sum(
        (r.execution_state or {}).get("final_protection", 0)
        for r in active_rows
        if r.user_id == row.user_id and active(r, now())
    )
    supplier, loss = await exposure(session)
    if (
        supplier + budget + protected + others
        > settings.SHARED_ALLOWANCE_PROVIDER_BUDGET_UNITS
    ):
        raise HTTPException(503, detail={"error": "beta_spend_paused"})
    global_loss = (
        settings.SHARED_ALLOWANCE_PROVIDER_BUDGET_UNITS
        * max(0, min(100, settings.SHARED_ALLOWANCE_RECOVERY_BUDGET_PERCENT))
        // 100
    )
    if loss + budget + protected + others > global_loss:
        raise HTTPException(503, detail={"error": "provider_failure_spend_paused"})
    if not row.risk_policy:
        return  # Legacy charging stays held; live no-hold finals still need protection.
    _, user_loss = await exposure(
        session, user_id=row.user_id, days=row.risk_policy["loss_window_days"]
    )
    if user_loss + budget + protected + own_others > row.risk_policy["user_loss_units"]:
        raise HTTPException(429, detail={"error": "user_supplier_spend_paused"})
