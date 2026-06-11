import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.conversion import conversion
import app.api.conversion_helpers as conversion_helpers
from app.api.dependencies import get_current_user
from app.db.database import get_session
from app.db.models import AppUser, Payment, PaymentMethod, PaymentProductType, RequestLedger
from app.db.subscription_tiers import SubscriptionStatus, SubscriptionTier, TierModelLimit, UserSubscription


async def _create_user(session: AsyncSession, telegram_id: int, *, campaign: str | None = None) -> AppUser:
    user = AppUser(
        telegram_id=telegram_id,
        campaign=campaign,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def _build_app(engine, user: AppUser) -> FastAPI:
    app = FastAPI()
    app.include_router(conversion, prefix="/api/v1")

    async def _fake_get_session():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_session] = _fake_get_session
    return app


@pytest.mark.asyncio
async def test_conversion_state_for_free_user_without_subscription():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = await _create_user(session, 722000001, campaign="ads_search_1")

    app = _build_app(engine, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/conversion/state")
        assert response.status_code == 200
        payload = response.json()

    await engine.dispose()

    assert payload["campaign"] == "ads_search_1"
    assert payload["has_sent_first_message"] is False
    assert payload["first_purchase_available"] is True
    assert payload["primary_subscription"] is None
    assert payload["payment_methods"]["methods_count"] == 0
    assert payload["payment_methods"]["has_default_method"] is False
    assert payload["payment_methods"]["default_method"] is None
    assert payload["payment_methods"]["renewal_action_hint"] == "none"
    assert payload["refund_status"] is None
    assert payload["offer_summary"]["primary_nudge"] == "premium_sample"
    assert payload["offer_summary"]["premium_sample_available"] is True
    assert payload["offer_summary"]["has_discount"] is False
    assert payload["offer_summary"]["refund_available"] is False
    assert payload["premium_sample"]["status"] == "available"
    assert payload["premium_sample"]["eligible"] is True
    assert payload["premium_sample"]["kinds"] == ["flagship_text"]
    assert payload["premium_sample"]["available_models"] == ["gpt-5.5", "gemini-3.1-pro-preview"]
    assert payload["premium_sample"]["default_model"] == "gpt-5.5"
    assert payload["premium_sample"]["remaining_uses_today"] == 1
    assert payload["premium_sample"]["next_reset_at"] is not None


@pytest.mark.asyncio
async def test_conversion_state_reports_primary_subscription_and_default_method():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = await _create_user(session, 722000002)
        user.has_sent_first_message = True
        session.add(user)
        await session.commit()
        await session.refresh(user)

        tier = SubscriptionTier(
            name="pro-499",
            name_ru="pro-499",
            description="Pro plan",
            description_ru="Pro plan",
            price_cents=499,
            index=20,
            is_recurring=True,
            is_public=True,
            is_active=True,
        )
        session.add(tier)
        await session.commit()
        await session.refresh(tier)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add(
            UserSubscription(
                user_id=user.id,
                tier_id=tier.id,
                status=SubscriptionStatus.active,
                started_at=now - timedelta(days=2),
                expires_at=now + timedelta(days=28),
                auto_renew_enabled=True,
            )
        )
        session.add(
            PaymentMethod(
                user_id=user.id,
                rebill_id="REBILL-CONV-1",
                type="card",
                card_type="Visa",
                pan="**** 4242",
                exp_date="1229",
                status="active",
                is_default=True,
                bound_at=now - timedelta(days=3),
            )
        )
        session.add(
            Payment(
                user_id=user.id,
                tier_name=tier.name,
                amount=49900,
                tbank_status="CONFIRMED",
                product_type=PaymentProductType.subscription,
            )
        )
        await session.commit()

    app = _build_app(engine, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/conversion/state")
        assert response.status_code == 200
        payload = response.json()

    await engine.dispose()

    assert payload["has_sent_first_message"] is True
    assert payload["first_purchase_available"] is False
    assert payload["primary_subscription"] is not None
    assert payload["primary_subscription"]["tier_name"] == "pro-499"
    assert payload["primary_subscription"]["renewal_state"] == "scheduled"
    assert payload["payment_methods"]["methods_count"] == 1
    assert payload["payment_methods"]["has_default_method"] is True
    assert payload["payment_methods"]["default_method"]["pan"] == "**** 4242"
    assert payload["payment_methods"]["renewal_action_hint"] == "scheduled"
    assert payload["refund_status"] is not None
    assert payload["refund_status"]["refundable"] is True
    assert payload["refund_status"]["refund_deadline_at"] is not None
    assert payload["offer_summary"]["primary_nudge"] == "refund_available"
    assert payload["offer_summary"]["premium_sample_available"] is False
    assert payload["offer_summary"]["refund_available"] is True
    assert payload["premium_sample"]["status"] == "ineligible"
    assert payload["premium_sample"]["reason"] == "already_subscribed"
    assert payload["premium_sample"]["eligible"] is False


@pytest.mark.asyncio
async def test_conversion_state_reports_consumed_premium_sample():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = await _create_user(session, 722000003)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add(
            RequestLedger(
                user_id=user.id,
                request_id="sample-consumed-1",
                model_name="gpt-5.5",
                feature="text",
                cost=1.0,
                access_path="premium_sample:flagship_text",
                state="consumed",
                created_at=now,
            )
        )
        await session.commit()

    app = _build_app(engine, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/conversion/state")
        assert response.status_code == 200
        payload = response.json()

    await engine.dispose()

    assert payload["premium_sample"]["status"] == "consumed"
    assert payload["premium_sample"]["eligible"] is False
    assert payload["premium_sample"]["reason"] == "already_used_today"
    assert payload["premium_sample"]["remaining_uses_today"] == 0
    assert payload["premium_sample"]["next_reset_at"] is not None
    assert payload["offer_summary"]["primary_nudge"] == "none"


@pytest.mark.asyncio
async def test_conversion_state_keeps_premium_sample_available_for_free_welcome_tier():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = await _create_user(session, 722000004)
        tier = SubscriptionTier(
            name="welcoming-bonus-free",
            name_ru="welcoming-bonus-free",
            description="Free welcome tier",
            description_ru="Free welcome tier",
            price_cents=0,
            index=0,
            is_recurring=False,
            is_public=True,
            is_active=True,
        )
        session.add(tier)
        await session.commit()
        await session.refresh(tier)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add(
            UserSubscription(
                user_id=user.id,
                tier_id=tier.id,
                status=SubscriptionStatus.active,
                started_at=now - timedelta(days=1),
                expires_at=now + timedelta(days=6),
                auto_renew_enabled=False,
            )
        )
        await session.commit()

    app = _build_app(engine, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/conversion/state")
        assert response.status_code == 200
        payload = response.json()

    await engine.dispose()

    assert payload["primary_subscription"] is not None
    assert payload["primary_subscription"]["tier_name"] == "welcoming-bonus-free"
    assert payload["premium_sample"]["status"] == "available"
    assert payload["premium_sample"]["eligible"] is True
    assert payload["premium_sample"]["reason"] == "available"


@pytest.mark.asyncio
async def test_conversion_state_keeps_premium_sample_available_when_free_tier_has_zero_flagship_cap():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = await _create_user(session, 722000005)
        tier = SubscriptionTier(
            name="welcoming-bonus-zero-flagship",
            name_ru="welcoming-bonus-zero-flagship",
            description="Free welcome tier with zero flagship cap",
            description_ru="Free welcome tier with zero flagship cap",
            price_cents=0,
            index=0,
            is_recurring=False,
            is_public=True,
            is_active=True,
        )
        session.add(tier)
        await session.commit()
        await session.refresh(tier)

        session.add(TierModelLimit(tier_id=tier.id, model_name="gpt-5.5", monthly_requests=0, daily_requests=0))
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add(
            UserSubscription(
                user_id=user.id,
                tier_id=tier.id,
                status=SubscriptionStatus.active,
                started_at=now - timedelta(days=1),
                expires_at=now + timedelta(days=6),
                auto_renew_enabled=False,
            )
        )
        await session.commit()

    app = _build_app(engine, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/conversion/state")
        assert response.status_code == 200
        payload = response.json()

    await engine.dispose()

    assert payload["primary_subscription"] is not None
    assert payload["primary_subscription"]["tier_name"] == "welcoming-bonus-zero-flagship"
    assert payload["premium_sample"]["status"] == "available"
    assert payload["premium_sample"]["eligible"] is True
    assert payload["premium_sample"]["reason"] == "available"


@pytest.mark.asyncio
async def test_conversion_event_endpoint_tracks_premium_sample_event(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = await _create_user(session, 722000006, campaign="ads_search_2")

    tracked: list[tuple[str, str, dict]] = []

    def _fake_track_event(key: str, user_id: str, tags: dict | None = None):
        tracked.append((key, user_id, tags or {}))

    monkeypatch.setattr(conversion_helpers, "track_event", _fake_track_event)

    app = _build_app(engine, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/conversion/events",
            json={
                "event": "premium_sample_clicked",
                "kind": "flagship_text",
                "model": "gpt-5.5",
                "surface": "home_chip",
                "status": "available",
            },
        )
        assert response.status_code == 200
        payload = response.json()

    await engine.dispose()

    assert payload == {"status": "ok"}
    assert tracked == [
        (
            "premium_sample_clicked",
            str(user.id),
            {
                "campaign": "ads_search_2",
                "kind": "flagship_text",
                "model": "gpt-5.5",
                "surface": "home_chip",
                "status": "available",
            },
        )
    ]
