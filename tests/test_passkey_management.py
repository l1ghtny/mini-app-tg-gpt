import importlib.util
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api import passkey_helpers
from app.api.auth import PasskeyRename, _passkey_view, delete_passkey, rename_passkey
from app.api.passkey_metadata import passkey_client
from app.db.models import AppUser, PasskeyCredential


@pytest.mark.parametrize(
    "ua,expected",
    [
        (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Version/26.0 Safari/605.1",
            ("Safari", "macOS"),
        ),
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0) Version/18.0 Safari/604.1",
            ("Safari", "iOS"),
        ),
        (
            "Mozilla/5.0 (iPad; CPU OS 18_0) CriOS/140.0 Safari/604.1",
            ("Chrome", "iPadOS"),
        ),
        (
            "Mozilla/5.0 (Windows NT 10.0) Chrome/140.0 Safari/537.36 Edg/140.0",
            ("Edge", "Windows"),
        ),
        (
            "Mozilla/5.0 (Linux; Android 15) Chrome/140.0 Safari/537.36",
            ("Chrome", "Android"),
        ),
        (None, (None, None)),
        ("unknown-client", (None, None)),
        ("Version/1.0", (None, None)),
    ],
)
def test_coarse_browser_context(ua, expected):
    assert passkey_client(ua) == expected


def credential(user, **extra):
    return PasskeyCredential(
        user_id=user.id, credential_id="a2V5", public_key=b"public", **extra
    )


def session_for(passkey):
    session = Mock()
    session.get = AsyncMock(return_value=passkey)
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.delete = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_registration_records_browser_without_persisting_localized_default(
    monkeypatch,
):
    user = AppUser()
    redis = AsyncMock()
    redis.getdel.return_value = json.dumps(
        {
            "user_id": str(user.id),
            "challenge": "eA",
            "rp_id": "app.example.com",
            "origin": "https://app.example.com",
        }
    )
    verification = SimpleNamespace(
        credential_id=b"key",
        credential_public_key=b"public",
        sign_count=0,
        credential_device_type=SimpleNamespace(value="multi_device"),
        credential_backed_up=True,
    )
    monkeypatch.setattr(
        passkey_helpers, "verify_registration_response", Mock(return_value=verification)
    )
    session = session_for(None)
    rows = Mock()
    rows.first.return_value = None
    session.exec = AsyncMock(return_value=rows)
    result = await passkey_helpers.finish_passkey_registration(
        session,
        redis,
        user=user,
        ceremony_id="test",
        credential={"response": {}},
        name=None,
        user_agent="Macintosh Version/26 Safari/605",
    )
    assert (result.created_browser, result.created_os, result.name) == (
        "Safari",
        "macOS",
        "",
    )
    assert result.last_used_browser is None
    assert result.rp_id == "app.example.com"


@pytest.mark.asyncio
async def test_rename_preserves_credential_and_context():
    user = AppUser()
    key = credential(
        user,
        name="Passkey",
        rp_id="app.example.com",
        created_browser="Safari",
        created_os="macOS",
    )
    session = session_for(key)
    result = await rename_passkey(
        key.id, PasskeyRename(name="  My MacBook  "), user, session
    )
    assert result.name == "My MacBook"
    assert result.created_browser == "Safari"
    assert result.rp_id == "app.example.com"
    assert key.credential_id == "a2V5"
    assert "public_key" not in result.model_dump()
    session.commit.assert_awaited_once()


@pytest.mark.parametrize("operation", ["rename", "delete"])
@pytest.mark.asyncio
async def test_mutations_cannot_target_another_accounts_key(operation):
    key = credential(AppUser())
    session = session_for(key)
    with pytest.raises(HTTPException) as exc:
        if operation == "rename":
            await rename_passkey(key.id, PasskeyRename(name="Mine"), AppUser(), session)
        else:
            await delete_passkey(key.id, AppUser(), session)
    assert exc.value.status_code == 404
    session.commit.assert_not_awaited()
    session.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_whitespace_name_cannot_replace_existing_name():
    user = AppUser()
    key = credential(user, name="Keep me")
    session = session_for(key)
    with pytest.raises(HTTPException):
        await rename_passkey(key.id, PasskeyRename(name="   "), user, session)
    assert key.name == "Keep me"
    session.commit.assert_not_awaited()
    with pytest.raises(ValidationError):
        PasskeyRename(name="x" * 81)


def test_legacy_metadata_stays_unknown():
    key = credential(AppUser(), created_at=datetime(2026, 7, 1))
    view = _passkey_view(key)
    assert view.rp_id is None
    assert view.created_browser is None
    assert view.created_os is None


@pytest.mark.asyncio
async def test_sign_in_records_latest_browser_without_changing_creation_context(
    monkeypatch,
):
    user = AppUser()
    key = credential(
        user, created_browser="Safari", created_os="macOS", rp_id="app.example.com"
    )
    session = session_for(user)
    rows = Mock()
    rows.first.return_value = key
    session.exec = AsyncMock(return_value=rows)
    redis = AsyncMock()
    redis.getdel.return_value = json.dumps(
        {
            "challenge": "eA",
            "rp_id": "app.example.com",
            "origin": "https://app.example.com",
        }
    )
    verification = SimpleNamespace(
        credential_id=b"key",
        new_sign_count=1,
        credential_device_type=SimpleNamespace(value="multi_device"),
        credential_backed_up=True,
    )
    monkeypatch.setattr(
        passkey_helpers,
        "verify_authentication_response",
        Mock(return_value=verification),
    )
    _, result = await passkey_helpers.finish_passkey_authentication(
        session,
        redis,
        ceremony_id="test",
        credential={"id": "a2V5", "response": {}},
        user_agent="Windows NT 10 Chrome/140 Safari/537",
    )
    assert (result.created_browser, result.created_os) == ("Safari", "macOS")
    assert (result.last_used_browser, result.last_used_os) == ("Chrome", "Windows")
    assert result.last_used_at is not None


def test_metadata_migration_is_additive_and_idempotent():
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = (
        Path(__file__).parents[1]
        / "migrations/versions/xw0e1f2a3b4c_passkey_display_context.py"
    )
    spec = importlib.util.spec_from_file_location("passkey_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with sa.create_engine("sqlite://").begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE passkey_credential (id INTEGER, public_key BLOB)"
        )
        connection.exec_driver_sql("INSERT INTO passkey_credential VALUES (1, X'CAFE')")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
        row = (
            connection.execute(sa.text("SELECT * FROM passkey_credential"))
            .mappings()
            .one()
        )
        assert row["public_key"] == bytes.fromhex("cafe")
        assert all(row[name] is None for name in migration._COLUMNS)
