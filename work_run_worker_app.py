from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.database import engine
from app.core.config import settings
from app.core.version import APP_VERSION
from app.core.work_run_worker_sentry import initialize_work_run_worker_sentry
from app.services.work_runs.worker import run_worker


logger = logging.getLogger(__name__)
initialize_work_run_worker_sentry(
    dsn=settings.SENTRY_DSN,
    environment=settings.ENVIRONMENT,
    release=os.getenv("SENTRY_RELEASE", "").strip() or APP_VERSION,
    deployment_channel=settings.DEPLOYMENT_CHANNEL,
    logger=logger,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop_event = asyncio.Event()
    worker_task = asyncio.create_task(run_worker(stop_event))
    app.state.worker_task = worker_task
    try:
        yield
    finally:
        stop_event.set()
        await worker_task


app = FastAPI(title="Lightny Work Run Worker", lifespan=lifespan)


def _require_running_worker() -> None:
    worker_task = getattr(app.state, "worker_task", None)
    if worker_task is None or worker_task.done():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="work_run_worker_not_running",
        )


@app.get("/health/live")
async def live() -> dict[str, str]:
    _require_running_worker()
    return {"status": "ok"}


@app.get("/health/ready")
async def ready() -> dict[str, str]:
    _require_running_worker()
    async with AsyncSession(engine) as session:
        await session.execute(text("SELECT 1"))
    return {"status": "ready"}
