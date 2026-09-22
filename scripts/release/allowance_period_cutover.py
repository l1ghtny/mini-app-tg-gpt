"""Durable, explicit allowance cutover after all application writers are updated.

Run in an updated backend environment: status, pause, enable, or resume-calendar.
Never use resume-calendar after any account has been aligned.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.db.allowance import AllowanceAccount, AllowanceControl, AllowanceRequest
from app.db.models import AppUser
from app.services import allowance


BALANCE_FIELDS = (
    "plan",
    "granted",
    "spent",
    "reserved",
    "luna_granted",
    "luna_spent",
    "luna_reserved",
)


def balances(rows):
    return {
        str(row.id): tuple(getattr(row, name) for name in BALANCE_FIELDS)
        for row in rows
    }


async def transition(session, action):
    await session.execute(text("SET LOCAL lock_timeout = '15s'"))
    await session.execute(text("SET LOCAL statement_timeout = '60s'"))
    control = (
        await session.exec(
            select(AllowanceControl)
            .where(AllowanceControl.id == "subscription_periods")
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).one()
    previous = control.period_mode
    if action == "pause":
        if previous not in {"calendar", "paused"}:
            raise RuntimeError(
                "Only the calendar compatibility phase may be paused by this cutover"
            )
        target = "paused"
    elif action == "resume-calendar":
        if previous not in {"calendar", "paused"}:
            raise RuntimeError(
                "Never revert an enabled subscription ledger to calendar writers"
            )
        aligned = (
            await session.exec(
                select(AllowanceAccount.id)
                .where(AllowanceAccount.subscription_anchor.is_not(None))
                .limit(1)
            )
        ).first()
        if aligned:
            raise RuntimeError(
                "Aligned accounts exist; rollback to calendar mode is unsafe"
            )
        target = "calendar"
    elif action == "enable":
        if previous == "subscription":
            await session.rollback()
            return {"mode": previous, "already_enabled": True}
        if previous != "paused":
            raise RuntimeError(
                "Pause both channels before enabling subscription periods"
            )
        active = (
            await session.exec(
                select(AllowanceRequest.id)
                .where(AllowanceRequest.status.in_(["reserved", "pending"]))
                .limit(1)
            )
        ).first()
        if active:
            raise RuntimeError(
                "Requests are still draining; leave paused and retry or resume-calendar"
            )
        rows = (await session.exec(select(AllowanceAccount).with_for_update())).all()
        before = balances(rows)
        if any(row.reserved or row.luna_reserved for row in rows):
            raise RuntimeError("Unresolved holds remain; reconcile before cutover")
        now = datetime.now(UTC).replace(tzinfo=None)
        users = {
            row.user_id
            for row in rows
            if row.plan != "starter" and row.scope == allowance.accounting_scope()
        }
        aligned_count = 0
        for user_id in sorted(users, key=str):
            await session.exec(
                select(AppUser).where(AppUser.id == user_id).with_for_update()
            )
            access = await allowance.private_access(session, user_id, now=now)
            if not access:
                continue
            row, _, _, _ = await allowance.subscription_account(
                session, user_id, allowance.accounting_scope(), access, now
            )
            if row:
                if row.plan != access.plan:
                    raise RuntimeError(
                        "Private capacity changed; review separately before aligning dates"
                    )
                aligned_count += 1
        await session.flush()
        after = balances((await session.exec(select(AllowanceAccount))).all())
        if before != after:
            raise RuntimeError(
                "Cutover changed financial balances or created an account; rolled back"
            )
        target = "subscription"
    else:
        raise ValueError("Unknown cutover action")
    control.period_mode = target
    control.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(control)
    await session.commit()
    result = {
        "previous_mode": previous,
        "mode": target,
        "at": control.updated_at.isoformat(),
    }
    if action == "enable":
        result.update(
            aligned_accounts=aligned_count, balance_preservation_verified=True
        )
    return result


async def status(session):
    await session.execute(text("SET TRANSACTION READ ONLY"))
    control = await session.get(AllowanceControl, "subscription_periods")
    active = (
        await session.exec(
            select(AllowanceRequest).where(
                AllowanceRequest.status.in_(["reserved", "pending"])
            )
        )
    ).all()
    rows = (await session.exec(select(AllowanceAccount))).all()
    result = {
        "mode": control.period_mode if control else None,
        "scope": allowance.accounting_scope(),
        "active_requests": len(active),
        "oldest_active_at": min((row.created_at for row in active), default=None),
        "accounts": len(rows),
        "aligned_accounts": sum(row.subscription_anchor is not None for row in rows),
        "spent": sum(row.spent for row in rows),
        "reserved": sum(row.reserved for row in rows),
        "luna_spent": sum(row.luna_spent for row in rows),
        "luna_reserved": sum(row.luna_reserved for row in rows),
    }
    await session.rollback()
    return result


async def main(action):
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            result = (
                await status(session)
                if action == "status"
                else await transition(session, action)
            )
            print(json.dumps(result, default=str))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=["status", "pause", "enable", "resume-calendar"]
    )
    asyncio.run(main(parser.parse_args().action))
