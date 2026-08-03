import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.core.deployment_channel import (
    blocked_beta_action,
    ensure_deployment_user_allowed,
)


@pytest.fixture(autouse=True)
def rebuild_test_db():
    """These pure policy tests do not need the repository-wide database reset."""


@pytest.fixture(autouse=True)
def restore_deployment_settings(monkeypatch):
    monkeypatch.setattr(settings, "DEPLOYMENT_CHANNEL", "production")
    monkeypatch.setattr(settings, "BETA_ALLOWED_USER_IDS", ())
    for name in (
        "BETA_ALLOW_PAYMENTS",
        "BETA_ALLOW_ACCOUNT_DELETION",
        "BETA_ALLOW_ADMIN_MUTATIONS",
        "BETA_ALLOW_EXTERNAL_SHARING",
        "BETA_ALLOW_IDENTITY_MUTATIONS",
        "BETA_ALLOW_ENTITLEMENT_MUTATIONS",
        "BETA_ALLOW_EMAIL_DELIVERY",
    ):
        monkeypatch.setattr(settings, name, False)


def test_production_does_not_apply_beta_restrictions():
    ensure_deployment_user_allowed(None)
    assert blocked_beta_action("/api/v1/payments/tbank/init", "POST") is None


def test_beta_user_allowlist_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "DEPLOYMENT_CHANNEL", "beta")

    with pytest.raises(HTTPException) as exc_info:
        ensure_deployment_user_allowed(SimpleNamespace(id=uuid.uuid4()))

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "beta_access_denied"


def test_beta_user_allowlist_accepts_internal_user_id(monkeypatch):
    user_id = uuid.uuid4()
    monkeypatch.setattr(settings, "DEPLOYMENT_CHANNEL", "beta")
    monkeypatch.setattr(settings, "BETA_ALLOWED_USER_IDS", (str(user_id),))

    ensure_deployment_user_allowed(SimpleNamespace(id=user_id))


@pytest.mark.parametrize(
    ("path", "method", "action"),
    (
        ("/api/v1/payments/tbank/init", "POST", "payments"),
        ("/api/v1/payments/tbank/webhook", "POST", "payments"),
        ("/api/v1/account", "DELETE", "account_deletion"),
        ("/api/v1/admin/broadcast/jobs", "POST", "admin_broadcast"),
        ("/api/v1/auth/identities/telegram/link", "POST", "identity_mutation"),
        ("/api/v1/auth/web/email/login/request", "POST", "email_delivery"),
        ("/api/v1/images/000/prepare-share", "POST", "external_sharing"),
        ("/api/v1/tiers/subscribe/000", "POST", "entitlement_mutation"),
        ("/api/v1/user/subscription/cancel", "POST", "entitlement_mutation"),
        ("/api/v1/access_codes/000/redeem", "POST", "entitlement_mutation"),
    ),
)
def test_beta_blocks_dangerous_mutations(monkeypatch, path, method, action):
    monkeypatch.setattr(settings, "DEPLOYMENT_CHANNEL", "beta")

    assert blocked_beta_action(path, method) == action


def test_beta_keeps_normal_work_enabled(monkeypatch):
    monkeypatch.setattr(settings, "DEPLOYMENT_CHANNEL", "beta")

    assert blocked_beta_action("/api/v1/conversations", "POST") is None
    assert blocked_beta_action("/api/v1/documents/upload", "POST") is None
    assert blocked_beta_action("/api/v1/payments/tbank/status/000", "GET") is None


def test_individual_beta_kill_switch_can_be_overridden(monkeypatch):
    monkeypatch.setattr(settings, "DEPLOYMENT_CHANNEL", "beta")
    monkeypatch.setattr(settings, "BETA_ALLOW_PAYMENTS", True)

    assert blocked_beta_action("/api/v1/payments/tbank/init", "POST") is None
