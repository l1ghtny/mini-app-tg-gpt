from datetime import UTC, datetime, timedelta
from sqlalchemy import func
from sqlmodel import select
from app.db.allowance import AllowanceRequest
from app.db.models import AppUser
from app.db.onboarding import OnboardingVisit

NUDGE_IDS = {"personalisation_v2", "folders_v2", "install_v2"}


async def progress(session, user_id, session_id):
    now = datetime.now(UTC).replace(tzinfo=None)
    await session.exec(select(AppUser).where(AppUser.id == user_id).with_for_update())
    latest = (await session.exec(select(OnboardingVisit).where(OnboardingVisit.user_id == user_id).order_by(OnboardingVisit.created_at.desc()).limit(1))).first()
    existing = await session.get(OnboardingVisit, (user_id, session_id))
    if not existing and (not latest or now-latest.created_at >= timedelta(minutes=30)):
        session.add(OnboardingVisit(user_id=user_id, session_id=session_id, created_at=now))
        await session.flush()
    answers, chats = (await session.exec(select(func.count(AllowanceRequest.id), func.count(func.distinct(AllowanceRequest.conversation_id))).where(AllowanceRequest.user_id == user_id, AllowanceRequest.status == "complete"))).one()
    sessions, days = (await session.exec(select(func.count(OnboardingVisit.session_id), func.count(func.distinct(func.date(OnboardingVisit.created_at)))).where(OnboardingVisit.user_id == user_id))).one()
    await session.commit()
    return dict(successful_answers=answers, successful_chats=chats, sessions=sessions, active_days=days)


async def claim_nudge(session, user_id, item):
    """One suggestion per 24h across tabs/devices; dismissals are permanent for v2."""
    if item not in NUDGE_IDS:
        return False
    user = (await session.exec(select(AppUser).where(AppUser.id == user_id).with_for_update().execution_options(populate_existing=True))).one()
    state = dict(user.onboarding_state or {})
    entry = state.get(item, {})
    if any(entry.get(key) for key in ("seen_at", "completed_at", "dismissed_at")):
        await session.commit()
        return False
    now = datetime.now(UTC)
    for key in NUDGE_IDS:
        timestamp = state.get(key, {}).get("seen_at")
        if timestamp:
            seen = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if now-seen < timedelta(days=1):
                await session.commit()
                return False
    state[item] = {**entry, "seen_at": now.isoformat(), "flow_version": 2}
    user.onboarding_state = state
    session.add(user)
    await session.commit()
    return True
