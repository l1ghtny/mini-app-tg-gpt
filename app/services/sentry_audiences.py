from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Optional
from urllib.parse import parse_qs, urlparse
import uuid

import requests

from app.core.config import settings


def _configured_projects() -> list[str]:
    raw = settings.SENTRY_PROJECT.strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _assert_sentry_configured() -> None:
    if not settings.SENTRY_AUTH_TOKEN:
        raise RuntimeError("SENTRY_AUTH_TOKEN is not configured")
    if not settings.SENTRY_ORG:
        raise RuntimeError("SENTRY_ORG is not configured")


def _metric_query(metric_name: str) -> str:
    return f"(metric.name:{metric_name}) metric.type:counter (!has:metric.unit OR metric.unit:none)"


def _count_field(metric_name: str) -> str:
    return f"count(metric.name,{metric_name},counter,none)"


def _extract_next_cursor(response: requests.Response) -> Optional[str]:
    next_link = response.links.get("next")
    if not next_link:
        return None
    if str(next_link.get("results", "")).lower() != "true":
        return None
    url = next_link.get("url")
    if not url:
        return None
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    cursor_values = query.get("cursor")
    return cursor_values[0] if cursor_values else None


def _fetch_metric_user_ids_sync(
    metric_name: str,
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    stats_period: Optional[str] = None,
    limit: int = 5000,
) -> set[uuid.UUID]:
    _assert_sentry_configured()
    url = f"{settings.SENTRY_BASE_URL.rstrip('/')}/api/0/organizations/{settings.SENTRY_ORG}/events/"
    headers = {
        "Authorization": f"Bearer {settings.SENTRY_AUTH_TOKEN}",
        "Accept": "application/json",
    }
    params: list[tuple[str, str]] = [
        ("field", "user_id"),
        ("field", _count_field(metric_name)),
        ("dataset", "tracemetrics"),
        ("query", _metric_query(metric_name)),
        ("per_page", "100"),
    ]
    projects = _configured_projects()
    if projects:
        for project in projects:
            params.append(("project", project))
    else:
        params.append(("project", "-1"))

    if start and end:
        params.append(("start", start.astimezone(UTC).isoformat()))
        params.append(("end", end.astimezone(UTC).isoformat()))
    else:
        params.append(("statsPeriod", stats_period or "24h"))

    results: set[uuid.UUID] = set()
    cursor: Optional[str] = None
    while len(results) < limit:
        request_params = list(params)
        if cursor:
            request_params.append(("cursor", cursor))
        response = requests.get(url, headers=headers, params=request_params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        for row in payload.get("data", []):
            raw_user_id = str(row.get("user_id") or "").strip()
            if not raw_user_id:
                continue
            try:
                results.add(uuid.UUID(raw_user_id))
            except ValueError:
                continue
            if len(results) >= limit:
                break
        cursor = _extract_next_cursor(response)
        if not cursor:
            break
    return results


async def fetch_metric_user_ids(
    metric_name: str,
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    stats_period: Optional[str] = None,
    limit: int = 5000,
) -> set[uuid.UUID]:
    return await asyncio.to_thread(
        _fetch_metric_user_ids_sync,
        metric_name,
        start=start,
        end=end,
        stats_period=stats_period,
        limit=limit,
    )


async def registered_but_not_opened_within_hours(hours: int, *, limit: int = 5000) -> set[uuid.UUID]:
    now = datetime.now(UTC)
    start = now - timedelta(hours=hours)
    registered_ids, opened_ids = await asyncio.gather(
        fetch_metric_user_ids("user_registered", start=start, end=now, limit=limit),
        fetch_metric_user_ids("app_opened", start=start, end=now, limit=limit),
    )
    return registered_ids - opened_ids


async def opened_within_days(days: int, *, limit: int = 5000) -> set[uuid.UUID]:
    return await fetch_metric_user_ids("app_opened", stats_period=f"{days}d", limit=limit)
