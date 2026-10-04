"""Authenticated, fail-closed owner reporting. No mutation endpoints."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.dependencies import get_current_user
from app.core.config import settings
from app.db.database import read_engine
from app.db.models import AppUser
from app.services import admin_dashboard as reports

router = APIRouter(prefix="/admin/dashboard", tags=["admin-dashboard"])


def is_dashboard_admin(user: AppUser) -> bool:
    return user.deleted_at is None and (
        str(user.id).lower() in settings.ADMIN_DASHBOARD_USER_IDS
        or (
            user.telegram_id is not None
            and str(user.telegram_id) in settings.ADMIN_DASHBOARD_TELEGRAM_IDS
        )
    )


async def require_dashboard_admin(user: AppUser = Depends(get_current_user)) -> AppUser:
    if not is_dashboard_admin(user):
        raise HTTPException(403, detail="admin_dashboard_forbidden")
    return user


async def reporting_session(
    response: Response,
    admin: AppUser = Depends(require_dashboard_admin),
):
    response.headers["Cache-Control"] = "private, no-store"
    async with AsyncSession(read_engine, expire_on_commit=False) as session:
        async with session.begin():
            await session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            await session.execute(text("SET LOCAL statement_timeout = '15s'"))
            yield session


def reporting_window(
    start: datetime | None = None,
    end: datetime | None = None,
    include_test: bool = False,
):
    end = end or datetime.now(UTC)
    start = start or end - timedelta(days=30)
    if start.tzinfo is None or end.tzinfo is None:
        raise HTTPException(422, detail="Use timestamps with a timezone")
    if not start < end or end - start > timedelta(days=366):
        raise HTTPException(422, detail="Choose a positive period of at most 366 days")
    return reports.Window(
        start.astimezone(UTC).replace(tzinfo=None),
        end.astimezone(UTC).replace(tzinfo=None),
        include_test,
    )


@router.get("/access")
async def access(response: Response, user: AppUser = Depends(get_current_user)):
    response.headers["Cache-Control"] = "private, no-store"
    return {"allowed": is_dashboard_admin(user)}


@router.get("/overview")
async def overview(
    window=Depends(reporting_window), session=Depends(reporting_session)
):
    return await reports.overview(session, window)


@router.get("/users")
async def users(
    window=Depends(reporting_window),
    session=Depends(reporting_session),
    search: str = Query("", max_length=100),
    cohort: Literal["all", "paying", "private", "trial", "test"] = "all",
    sort: Literal[
        "cost", "tasks", "charged", "purchases", "remaining", "last_active"
    ] = "cost",
    offset: int = Query(0, ge=0, le=100000),
    limit: int = Query(25, ge=1, le=100),
):
    return await reports.users(
        session,
        window,
        search=search,
        cohort=cohort,
        sort=sort,
        offset=offset,
        limit=limit,
    )


@router.get("/users/{user_id}")
async def user_detail(
    user_id: uuid.UUID,
    window=Depends(reporting_window),
    session=Depends(reporting_session),
):
    return await reports.user_detail(session, window, user_id)


@router.get("/users/{user_id}/tasks")
async def tasks(
    user_id: uuid.UUID,
    window=Depends(reporting_window),
    session=Depends(reporting_session),
    offset: int = Query(0, ge=0, le=100000),
    limit: int = Query(20, ge=1, le=100),
):
    return await reports.tasks(session, window, user_id, offset, limit)


@router.get("/tasks/{task_id}/attempts")
async def attempts(task_id: uuid.UUID, session=Depends(reporting_session)):
    return await reports.attempts(session, task_id)


@router.get("/purchases")
async def purchases(
    window=Depends(reporting_window),
    session=Depends(reporting_session),
    user_id: uuid.UUID | None = None,
    status: str = Query("", max_length=40),
    offset: int = Query(0, ge=0, le=100000),
    limit: int = Query(25, ge=1, le=100),
):
    return await reports.purchases(
        session, window, user_id=user_id, status=status, offset=offset, limit=limit
    )


@router.get("/settings")
async def configuration(session=Depends(reporting_session)):
    return await reports.configuration(session)
