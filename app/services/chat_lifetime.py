"""Lifetime shared by ordinary chat generation and deletion protection."""

from datetime import datetime, timedelta, timezone

# Ordinary replies have a hard deadline; durable Work runs have their own lifecycle.
CHAT_GENERATION_LIFETIME = timedelta(hours=24)
CHAT_CLEANUP_GRACE = timedelta(minutes=5)


def generation_seconds_remaining(started_at: datetime | None) -> float:
    now = datetime.now(timezone.utc)
    start = started_at or now
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return max(0.0, (start + CHAT_GENERATION_LIFETIME - now).total_seconds())


def chat_busy_cutoff() -> datetime:
    return (datetime.now(timezone.utc) - CHAT_GENERATION_LIFETIME - CHAT_CLEANUP_GRACE).replace(tzinfo=None)
