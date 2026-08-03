from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status

from app.core.config import settings


@dataclass(frozen=True)
class BetaMutationRule:
    action: str
    setting_name: str


def is_beta_channel() -> bool:
    return settings.DEPLOYMENT_CHANNEL == "beta"


def ensure_deployment_user_allowed(user: Any | None) -> None:
    if not is_beta_channel():
        return

    user_id = str(getattr(user, "id", "")).lower()
    if not user_id or user_id not in settings.BETA_ALLOWED_USER_IDS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="beta_access_denied",
        )


def _mutation_rule(path: str, method: str) -> BetaMutationRule | None:
    normalized_path = path.rstrip("/") or "/"
    normalized_method = method.upper()
    if normalized_method in {"GET", "HEAD", "OPTIONS"}:
        return None

    if normalized_path.startswith("/api/v1/payments/tbank"):
        return BetaMutationRule("payments", "BETA_ALLOW_PAYMENTS")
    if normalized_method == "DELETE" and normalized_path == "/api/v1/account":
        return BetaMutationRule("account_deletion", "BETA_ALLOW_ACCOUNT_DELETION")
    if normalized_path.startswith("/api/v1/admin/broadcast"):
        return BetaMutationRule("admin_broadcast", "BETA_ALLOW_ADMIN_MUTATIONS")
    if normalized_path.startswith("/api/v1/auth/identities/"):
        return BetaMutationRule("identity_mutation", "BETA_ALLOW_IDENTITY_MUTATIONS")
    if normalized_path in {
        "/api/v1/auth/web/email/request",
        "/api/v1/auth/web/email/login/request",
        "/api/v1/auth/web/email/link/request",
    }:
        return BetaMutationRule("email_delivery", "BETA_ALLOW_EMAIL_DELIVERY")
    if (
        normalized_method == "POST"
        and normalized_path.startswith("/api/v1/images/")
        and normalized_path.endswith("/prepare-share")
    ):
        return BetaMutationRule("external_sharing", "BETA_ALLOW_EXTERNAL_SHARING")
    if (
        normalized_path.startswith("/api/v1/tiers/subscribe/")
        or normalized_path == "/api/v1/user/subscription/cancel"
        or (
            normalized_path.startswith("/api/v1/access_codes/")
            and normalized_method == "POST"
        )
    ):
        return BetaMutationRule(
            "entitlement_mutation", "BETA_ALLOW_ENTITLEMENT_MUTATIONS"
        )
    return None


def blocked_beta_action(path: str, method: str) -> str | None:
    if not is_beta_channel():
        return None
    rule = _mutation_rule(path, method)
    if not rule or getattr(settings, rule.setting_name):
        return None
    return rule.action
