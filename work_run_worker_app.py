from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.database import engine
from app.services.work_runs.worker import run_worker


@asynccontextmanager
async def lifespan(_: FastAPI):
    stop_event = asyncio.Event()
    worker_task = asyncio.create_task(run_worker(stop_event))
    try:
        yield
    finally:
        stop_event.set()
        await worker_task


app = FastAPI(title="Lightny Work Run Worker", lifespan=lifespan)


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def ready() -> dict[str, str]:
    async with AsyncSession(engine) as session:
        await session.execute(text("SELECT 1"))
    return {"status": "ready"}
