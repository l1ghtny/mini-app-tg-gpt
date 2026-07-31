import json
import secrets
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import (
    InvalidAuthenticationResponse,
    InvalidRegistrationResponse,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.core.config import settings
from app.db import models

_PASSKEY_PREFIX = "passkey:ceremony"
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def resolve_passkey_context(request: Request) -> tuple[str, str]:
    origin = (request.headers.get("origin") or "").rstrip("/")
    if not origin or origin not in settings.PASSKEY_ALLOWED_ORIGINS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="passkey_origin_not_allowed"
        )

    hostname = (urlsplit(origin).hostname or "").lower()
    configured_rp_id = settings.PASSKEY_RP_ID
    if configured_rp_id:
        if hostname != configured_rp_id and not hostname.endswith(
            f".{configured_rp_id}"
        ):
            raise HTTPException(status_code=503, detail="passkey_rp_id_mismatch")
        return origin, configured_rp_id

    if (settings.DEBUG_MODE or settings.TEST_ENV) and hostname in _LOOPBACK_HOSTS:
        return origin, hostname
    raise HTTPException(status_code=503, detail="passkey_not_configured")


def _ceremony_key(kind: str, ceremony_id: str) -> str:
    return f"{_PASSKEY_PREFIX}:{kind}:{ceremony_id}"


async def _store_ceremony(
    redis: Redis,
    *,
    kind: str,
    challenge: bytes,
    origin: str,
    rp_id: str,
    user_id: uuid.UUID | None = None,
) -> str:
    ceremony_id = secrets.token_urlsafe(24)
    state = {
        "challenge": bytes_to_base64url(challenge),
        "origin": origin,
        "rp_id": rp_id,
        "user_id": str(user_id) if user_id else None,
    }
    stored = await redis.set(
        _ceremony_key(kind, ceremony_id),
        json.dumps(state),
        ex=settings.PASSKEY_CHALLENGE_TTL_SECONDS,
        nx=True,
    )
    if not stored:
        raise HTTPException(status_code=503, detail="passkey_challenge_unavailable")
    return ceremony_id


async def _consume_ceremony(redis: Redis, *, kind: str, ceremony_id: str) -> dict:
    raw = await redis.getdel(_ceremony_key(kind, ceremony_id))
    if not raw:
        raise HTTPException(status_code=400, detail="passkey_challenge_expired")
    try:
        state = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail="passkey_challenge_invalid"
        ) from exc
    if not isinstance(state, dict):
        raise HTTPException(status_code=400, detail="passkey_challenge_invalid")
    return state


def _transports(values: list[str] | None) -> list[AuthenticatorTransport]:
    transports: list[AuthenticatorTransport] = []
    for value in values or []:
        try:
            transports.append(AuthenticatorTransport(value))
        except ValueError:
            continue
    return transports


async def begin_passkey_registration(
    session: AsyncSession,
    redis: Redis,
    *,
    user: models.AppUser,
    origin: str,
    rp_id: str,
) -> tuple[str, dict]:
    existing = (
        await session.exec(
            select(models.PasskeyCredential).where(
                models.PasskeyCredential.user_id == user.id
            )
        )
    ).all()
    identity = (
        await session.exec(
            select(models.UserIdentity).where(
                models.UserIdentity.user_id == user.id,
                models.UserIdentity.provider == "email",
            )
        )
    ).first()
    display_name = (
        identity.email
        if identity and identity.email
        else user.telegram_username or user.telegram_first_name or "Lightny AI user"
    )
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=settings.PASSKEY_RP_NAME,
        user_id=user.id.bytes,
        user_name=display_name,
        user_display_name=display_name,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(
                id=base64url_to_bytes(item.credential_id),
                transports=_transports(item.transports),
            )
            for item in existing
        ],
    )
    ceremony_id = await _store_ceremony(
        redis,
        kind="registration",
        challenge=options.challenge,
        origin=origin,
        rp_id=rp_id,
        user_id=user.id,
    )
    return ceremony_id, json.loads(options_to_json(options))


async def finish_passkey_registration(
    session: AsyncSession,
    redis: Redis,
    *,
    user: models.AppUser,
    ceremony_id: str,
    credential: dict,
    name: str | None,
) -> models.PasskeyCredential:
    state = await _consume_ceremony(redis, kind="registration", ceremony_id=ceremony_id)
    if state.get("user_id") != str(user.id):
        raise HTTPException(
            status_code=403, detail="passkey_registration_user_mismatch"
        )
    try:
        verification = verify_registration_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(state["challenge"]),
            expected_rp_id=state["rp_id"],
            expected_origin=state["origin"],
            require_user_verification=True,
        )
    except (InvalidRegistrationResponse, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail="passkey_registration_invalid"
        ) from exc

    credential_id = bytes_to_base64url(verification.credential_id)
    existing = (
        await session.exec(
            select(models.PasskeyCredential).where(
                models.PasskeyCredential.credential_id == credential_id
            )
        )
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="passkey_already_registered")

    response = credential.get("response") if isinstance(credential, dict) else None
    raw_transports = response.get("transports") if isinstance(response, dict) else []
    transports = [item.value for item in _transports(raw_transports)]
    label = (name or "").strip()[:80] or "Passkey"
    passkey = models.PasskeyCredential(
        user_id=user.id,
        credential_id=credential_id,
        public_key=verification.credential_public_key,
        sign_count=verification.sign_count,
        transports=transports,
        device_type=verification.credential_device_type.value,
        backed_up=verification.credential_backed_up,
        name=label,
    )
    session.add(passkey)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="passkey_already_registered"
        ) from exc
    await session.refresh(passkey)
    return passkey


async def begin_passkey_authentication(
    redis: Redis,
    *,
    origin: str,
    rp_id: str,
) -> tuple[str, dict]:
    options = generate_authentication_options(
        rp_id=rp_id,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    ceremony_id = await _store_ceremony(
        redis,
        kind="authentication",
        challenge=options.challenge,
        origin=origin,
        rp_id=rp_id,
    )
    return ceremony_id, json.loads(options_to_json(options))


async def finish_passkey_authentication(
    session: AsyncSession,
    redis: Redis,
    *,
    ceremony_id: str,
    credential: dict,
) -> tuple[models.AppUser, models.PasskeyCredential]:
    state = await _consume_ceremony(
        redis, kind="authentication", ceremony_id=ceremony_id
    )
    credential_id = credential.get("id") if isinstance(credential, dict) else None
    if not isinstance(credential_id, str) or not credential_id:
        raise HTTPException(status_code=400, detail="passkey_authentication_invalid")
    passkey = (
        await session.exec(
            select(models.PasskeyCredential).where(
                models.PasskeyCredential.credential_id == credential_id
            )
        )
    ).first()
    if not passkey:
        raise HTTPException(status_code=400, detail="passkey_authentication_invalid")

    response = credential.get("response")
    user_handle = response.get("userHandle") if isinstance(response, dict) else None
    if user_handle and base64url_to_bytes(user_handle) != passkey.user_id.bytes:
        raise HTTPException(status_code=400, detail="passkey_authentication_invalid")
    try:
        verification = verify_authentication_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(state["challenge"]),
            expected_rp_id=state["rp_id"],
            expected_origin=state["origin"],
            credential_public_key=passkey.public_key,
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=True,
        )
    except (InvalidAuthenticationResponse, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail="passkey_authentication_invalid"
        ) from exc

    if bytes_to_base64url(verification.credential_id) != passkey.credential_id:
        raise HTTPException(status_code=400, detail="passkey_authentication_invalid")
    user = await session.get(models.AppUser, passkey.user_id)
    if not user or user.deleted_at is not None:
        raise HTTPException(status_code=400, detail="passkey_authentication_invalid")

    passkey.sign_count = verification.new_sign_count
    passkey.device_type = verification.credential_device_type.value
    passkey.backed_up = verification.credential_backed_up
    passkey.last_used_at = _utcnow_naive()
    session.add(passkey)
    await session.commit()
    await session.refresh(passkey)
    return user, passkey
