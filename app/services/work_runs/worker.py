from __future__ import annotations

import asyncio
import logging
import os
import socket
from datetime import timedelta

from redis.asyncio import Redis
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.database import engine
from app.db.models import WorkRun, utcnow_naive
from app.redis.settings import settings as redis_settings
from app.services.work_runs.contracts import WorkRunStatus
from app.services.work_runs.service import fail_run, process_comparison_run


logger = logging.getLogger(__name__)
_CLAIMABLE_STATUSES = (
    WorkRunStatus.QUEUED.value,
    WorkRunStatus.RUNNING.value,
    WorkRunStatus.VALIDATING.value,
    WorkRunStatus.STORING.value,
    WorkRunStatus.CANCELLING.value,
)


def worker_id() -> str:
    return os.getenv("WORK_RUN_WORKER_ID") or f"{socket.gethostname()}:{os.getpid()}"


async def claim_next_run(session: AsyncSession, *, executor_id: str) -> WorkRun | None:
    now = utcnow_naive()
    statement = (
        select(WorkRun)
        .where(
            col(WorkRun.status).in_(_CLAIMABLE_STATUSES),
            (WorkRun.lease_expires_at.is_(None)) | (WorkRun.lease_expires_at < now),
        )
        .order_by(WorkRun.queued_at, WorkRun.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    run = (await session.exec(statement)).first()
    if run is None:
        await session.rollback()
        return None
    run.worker_id = executor_id
    run.lease_expires_at = now + timedelta(minutes=10)
    run.attempt_count += 1
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


async def run_worker(stop_event: asyncio.Event) -> None:
    executor_id = worker_id()
    redis = Redis.from_url(redis_settings.REDIS_URL, decode_responses=True)
    logger.info("work-run worker started", extra={"worker_id": executor_id})
    try:
        while not stop_event.is_set():
            async with AsyncSession(engine, expire_on_commit=False) as session:
                run = await claim_next_run(session, executor_id=executor_id)
                if run is None:
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=2)
                    except TimeoutError:
                        pass
                    continue
                try:
                    await process_comparison_run(
                        session=session,
                        redis=redis,
                        run=run,
                        worker_id=executor_id,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception(
                        "work-run execution failed",
                        extra={"work_run_id": str(run.id), "worker_id": executor_id},
                    )
                    await session.rollback()
                    failed_run = await session.get(WorkRun, run.id)
                    if failed_run is not None:
                        await fail_run(
                            session=session,
                            redis=redis,
                            run=failed_run,
                            error=exc,
                        )
    finally:
        await redis.aclose()
        logger.info("work-run worker stopped", extra={"worker_id": executor_id})
