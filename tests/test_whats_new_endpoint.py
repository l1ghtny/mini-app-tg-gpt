import os
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.dependencies import get_current_user
from app.api.whats_new_helpers import _is_allowed_open_url
from app.api.whats_new import whats_new
from app.db.database import get_session
from app.db.models import AppUser, UserWhatsNewState, WhatsNewItem


def _ts(days: int) -> datetime:
    return (datetime.now(UTC) + timedelta(days=days)).replace(tzinfo=None, microsecond=0)


async def _create_user(session: AsyncSession, telegram_id: int) -> AppUser:
    user = AppUser(telegram_id=telegram_id)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _seed_items(session: AsyncSession, user: AppUser) -> None:
    session.add_all([
        WhatsNewItem(
            id="2026-05-folders",
            kind="feature",
            title_en="Folders for your chats",
            title_ru="Папки для чатов",
            body_en="Group prompts into folders.",
            body_ru="Группируйте промпты по папкам.",
            icon="folder",
            published_at=_ts(-1),
            pinned=False,
            is_active=True,
        ),
        WhatsNewItem(
            id="2026-04-promo",
            kind="promo",
            title_en="Upgrade and unlock GPT-5.5",
            body_en="Try premium features.",
            published_at=_ts(-5),
            pinned=True,
            audience_plans=["free"],
            cta_label_en="Upgrade",
            cta_kind="open_subscription",
            is_active=True,
        ),
        WhatsNewItem(
            id="2026-03-link",
            kind="announcement",
            title_en="External link",
            body_en="Should be hidden unless open_url is enabled.",
            published_at=_ts(-3),
            pinned=False,
            cta_label_en="Read",
            cta_kind="open_url",
            cta_value="https://example.com/changelog",
            is_active=True,
        ),
    ])
    session.add(UserWhatsNewState(user_id=user.id, seen_up_to=_ts(-4)))
    await session.commit()


def _build_app(engine, user: AppUser) -> FastAPI:
    app = FastAPI()
    app.include_router(whats_new, prefix="/api/v1")

    async def _fake_get_session():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_session] = _fake_get_session
    return app


def test_open_url_supported_prefixes():
    assert _is_allowed_open_url("https://example.com/page")
    assert _is_allowed_open_url("http://example.com/page")
    assert _is_allowed_open_url("tg://resolve?domain=abc")
    assert _is_allowed_open_url("t.me/abc")
    assert _is_allowed_open_url("app://settings/subscription")
    assert not _is_allowed_open_url("javascript:alert(1)")
    assert not _is_allowed_open_url("ftp://example.com")


@pytest.mark.asyncio
async def test_whats_new_list_localized_and_unseen_count():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = await _create_user(session, 721220001)
        await _seed_items(session, user)

    app = _build_app(engine, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/whats-new?lang=ru&limit=5")

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["id"] == "2026-04-promo"
    assert payload["items"][1]["title"] == "Папки для чатов"
    assert payload["has_unseen"] is True
    assert payload["unseen_count"] == 2
    assert payload["items"][2]["cta"]["kind"] == "open_url"
    assert payload["items"][2]["cta"]["value"] == "https://example.com/changelog"

    await engine.dispose()


@pytest.mark.asyncio
async def test_whats_new_since_keeps_pinned():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = await _create_user(session, 721220002)
        await _seed_items(session, user)

    app = _build_app(engine, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/whats-new?since={_ts(-2).isoformat()}")

    assert response.status_code == 200
    payload = response.json()
    ids = [item["id"] for item in payload["items"]]
    assert "2026-04-promo" in ids  # pinned stays visible
    assert "2026-05-folders" in ids
    assert "2026-03-link" not in ids

    await engine.dispose()


@pytest.mark.asyncio
async def test_mark_seen_updates_watermark():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = await _create_user(session, 721220003)
        await _seed_items(session, user)

    app = _build_app(engine, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/whats-new/seen", json={"ids": ["2026-05-folders"]})
        assert response.status_code == 200
        first_seen = response.json()["seen_up_to"]
        assert first_seen is not None

        response2 = await client.post("/api/v1/whats-new/seen", json={"up_to": _ts(-10).isoformat()})
        assert response2.status_code == 200
        assert response2.json()["seen_up_to"] == first_seen

    await engine.dispose()
