import os
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.web_auth_helpers import (
    _callback_url,
    build_user_profile,
    consume_magic_link,
    issue_magic_link,
    normalize_email,
)
from app.core.config import settings
from app.db.models import AppUser, UserIdentity, WebAuthChallenge


def test_app_user_id_is_required_and_only_telegram_id_is_optional():
    user = AppUser()

    assert isinstance(user.id, uuid.UUID)
    assert user.telegram_id is None
    assert AppUser.model_fields["id"].annotation is uuid.UUID
    assert AppUser.__table__.c.id.nullable is False
    assert AppUser.__table__.c.telegram_id.nullable is True


def test_normalize_email_is_case_insensitive_and_rejects_invalid_values():
    assert normalize_email("  User@Example.COM ") == "user@example.com"
    with pytest.raises(HTTPException) as exc_info:
        normalize_email("not-an-email")
    assert exc_info.value.status_code == 422


def test_callback_url_keeps_token_out_of_the_query_string(monkeypatch):
    monkeypatch.setattr(
        settings,
        "WEB_AUTH_CALLBACK_URL",
        "https://app.example.com/auth/callback?source=email",
    )

    callback = _callback_url("secret-token")

    assert callback == (
        "https://app.example.com/auth/callback?source=email#token=secret-token"
    )


@pytest.mark.asyncio
async def test_magic_link_creates_web_user_and_is_single_use(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    if not test_db_url:
        pytest.skip("TEST_DATABASE_URL is required for integration tests")
    monkeypatch.setattr(settings, "TEST_ENV", True)
    monkeypatch.setattr(settings, "STARTER_BUNDLE_NAME", "free")
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        token = await issue_magic_link(
            session,
            email="New.User@Example.com",
            target_user=None,
        )
        assert token

        access_token, bonus_granted, user = await consume_magic_link(
            session, token=token
        )
        assert access_token
        assert bonus_granted is True
        assert user.telegram_id is None

        identity = (
            await session.exec(
                select(UserIdentity).where(
                    UserIdentity.provider == "email",
                    UserIdentity.subject == "new.user@example.com",
                )
            )
        ).one()
        assert identity.user_id == user.id

        profile = await build_user_profile(session, user)
        assert profile["email"] == "new.user@example.com"
        assert profile["auth_providers"] == ["email"]

        with pytest.raises(HTTPException) as replay_error:
            await consume_magic_link(session, token=token)
        assert replay_error.value.status_code == 400

    await engine.dispose()


@pytest.mark.asyncio
async def test_magic_link_can_attach_email_to_telegram_user(monkeypatch):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    if not test_db_url:
        pytest.skip("TEST_DATABASE_URL is required for integration tests")
    monkeypatch.setattr(settings, "TEST_ENV", True)
    monkeypatch.setattr(settings, "STARTER_BUNDLE_NAME", "free")
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=799000001, telegram_first_name="Existing")
        session.add(user)
        await session.commit()
        await session.refresh(user)

        token = await issue_magic_link(
            session,
            email="existing@example.com",
            target_user=user,
        )
        assert token

        _, _, linked_user = await consume_magic_link(session, token=token)
        assert linked_user.id == user.id
        assert linked_user.telegram_id == 799000001

        identities = (
            await session.exec(
                select(UserIdentity).where(UserIdentity.user_id == user.id)
            )
        ).all()
        assert {(item.provider, item.subject) for item in identities} == {
            ("email", "existing@example.com")
        }
        challenge = (await session.exec(select(WebAuthChallenge))).one()
        assert challenge.consumed_at is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_magic_link_conflict_preserves_accounts_and_consumes_challenge(
    monkeypatch,
):
    test_db_url = os.getenv("TEST_DATABASE_URL")
    if not test_db_url:
        pytest.skip("TEST_DATABASE_URL is required for integration tests")
    monkeypatch.setattr(settings, "TEST_ENV", True)
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        email_user = AppUser(telegram_id=None)
        telegram_user = AppUser(telegram_id=799000002, telegram_first_name="Target")
        session.add(email_user)
        session.add(telegram_user)
        await session.flush()
        session.add(
            UserIdentity(
                user_id=email_user.id,
                provider="email",
                subject="conflict@example.com",
                email="conflict@example.com",
            )
        )
        await session.commit()

        token = await issue_magic_link(
            session,
            email="conflict@example.com",
            target_user=telegram_user,
        )
        assert token

        with pytest.raises(HTTPException) as exc_info:
            await consume_magic_link(session, token=token)
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail == "account_merge_required"

        challenge = (
            await session.exec(
                select(WebAuthChallenge).where(
                    WebAuthChallenge.email == "conflict@example.com"
                )
            )
        ).one()
        assert challenge.consumed_at is not None
        assert (await session.get(AppUser, email_user.id)) is not None
        assert (await session.get(AppUser, telegram_user.id)) is not None

    await engine.dispose()
