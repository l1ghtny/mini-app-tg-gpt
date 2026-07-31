import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from fastapi import HTTPException
from jose import JWTError, jwt
from redis.asyncio import Redis

from app.core.config import settings


_STATE_PREFIX = "telegram:oidc:state"
_JWKS_CACHE_KEY = "telegram:oidc:jwks"


@dataclass(frozen=True)
class TelegramOidcIdentity:
    telegram_id: int
    profile: dict[str, str | None]
    return_to: str


def normalize_return_to(value: str | None) -> str:
    parsed = urlsplit(value or "/")
    if (
        parsed.scheme
        or parsed.netloc
        or not parsed.path.startswith("/")
        or parsed.path.startswith("//")
        or "\\" in parsed.path
        or any(ord(character) < 32 for character in parsed.path)
    ):
        return "/"
    return urlunsplit(("", "", parsed.path, parsed.query, ""))


def frontend_redirect(return_to: str, result: str) -> str:
    base = settings.WEBAPP_URL.rstrip("/")
    if not base:
        raise RuntimeError("WEBAPP_URL must be configured")
    target = f"{base}{normalize_return_to(return_to)}"
    separator = "&" if "?" in target else "?"
    return f"{target}{separator}{urlencode({'telegram_login': result})}"


def _require_config() -> None:
    if not settings.WEB_AUTH_ENABLED or not settings.TELEGRAM_OIDC_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")
    required = (
        settings.TELEGRAM_OIDC_CLIENT_ID,
        settings.TELEGRAM_OIDC_CLIENT_SECRET,
        settings.TELEGRAM_OIDC_REDIRECT_URI,
    )
    if not all(required):
        raise HTTPException(status_code=503, detail="telegram_login_not_configured")


def _state_key(state: str) -> str:
    return f"{_STATE_PREFIX}:{state}"


async def begin_telegram_oidc(redis: Redis, *, return_to: str | None = None) -> str:
    _require_config()
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    payload = {
        "nonce": nonce,
        "code_verifier": code_verifier,
        "return_to": normalize_return_to(return_to),
    }
    await redis.set(
        _state_key(state),
        json.dumps(payload, separators=(",", ":")),
        ex=settings.TELEGRAM_OIDC_STATE_TTL_SECONDS,
    )
    query = urlencode(
        {
            "client_id": settings.TELEGRAM_OIDC_CLIENT_ID,
            "redirect_uri": settings.TELEGRAM_OIDC_REDIRECT_URI,
            "response_type": "code",
            "scope": settings.TELEGRAM_OIDC_SCOPES,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{settings.TELEGRAM_OIDC_AUTH_URL}?{query}"


async def _consume_state(redis: Redis, state: str) -> dict[str, str]:
    raw = await redis.getdel(_state_key(state))
    if not raw:
        raise HTTPException(status_code=400, detail="telegram_login_state_invalid")
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=400, detail="telegram_login_state_invalid"
        ) from exc
    if not isinstance(payload, dict) or not all(
        isinstance(payload.get(key), str)
        for key in ("nonce", "code_verifier", "return_to")
    ):
        raise HTTPException(status_code=400, detail="telegram_login_state_invalid")
    return payload


async def _exchange_code(code: str, code_verifier: str) -> str:
    try:
        async with httpx.AsyncClient(
            timeout=settings.TELEGRAM_OIDC_HTTP_TIMEOUT_SECONDS
        ) as client:
            response = await client.post(
                settings.TELEGRAM_OIDC_TOKEN_URL,
                auth=httpx.BasicAuth(
                    settings.TELEGRAM_OIDC_CLIENT_ID,
                    settings.TELEGRAM_OIDC_CLIENT_SECRET,
                ),
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": settings.TELEGRAM_OIDC_REDIRECT_URI,
                    "client_id": settings.TELEGRAM_OIDC_CLIENT_ID,
                    "code_verifier": code_verifier,
                },
            )
            response.raise_for_status()
            id_token = response.json().get("id_token")
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=502, detail="telegram_login_unavailable"
        ) from exc
    if not isinstance(id_token, str) or not id_token:
        raise HTTPException(status_code=502, detail="telegram_login_invalid_response")
    return id_token


async def _load_jwks(redis: Redis) -> dict:
    cached = await redis.get(_JWKS_CACHE_KEY)
    if cached:
        try:
            value = json.loads(cached)
            if isinstance(value, dict) and isinstance(value.get("keys"), list):
                return value
        except (TypeError, json.JSONDecodeError):
            pass
    try:
        async with httpx.AsyncClient(
            timeout=settings.TELEGRAM_OIDC_HTTP_TIMEOUT_SECONDS
        ) as client:
            response = await client.get(settings.TELEGRAM_OIDC_JWKS_URL)
            response.raise_for_status()
            value = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=502, detail="telegram_login_unavailable"
        ) from exc
    if not isinstance(value, dict) or not isinstance(value.get("keys"), list):
        raise HTTPException(status_code=502, detail="telegram_login_invalid_response")
    await redis.set(_JWKS_CACHE_KEY, json.dumps(value), ex=3600)
    return value


async def _verify_id_token(redis: Redis, id_token: str, nonce: str) -> dict:
    try:
        header = jwt.get_unverified_header(id_token)
        algorithm = header.get("alg")
        kid = header.get("kid")
        if algorithm not in {"RS256", "ES256"} or not isinstance(kid, str):
            raise JWTError("Unsupported Telegram signing key")
        jwks = await _load_jwks(redis)
        signing_key = next(
            key
            for key in jwks["keys"]
            if key.get("kid") == kid and key.get("alg", algorithm) == algorithm
        )
        claims = jwt.decode(
            id_token,
            signing_key,
            algorithms=[algorithm],
            audience=settings.TELEGRAM_OIDC_CLIENT_ID,
            issuer=settings.TELEGRAM_OIDC_ISSUER,
        )
    except (JWTError, KeyError, StopIteration, TypeError) as exc:
        raise HTTPException(
            status_code=403, detail="telegram_login_token_invalid"
        ) from exc
    if claims.get("nonce") != nonce:
        raise HTTPException(status_code=403, detail="telegram_login_token_invalid")
    return claims


async def complete_telegram_oidc(
    redis: Redis,
    *,
    code: str,
    state: str,
) -> TelegramOidcIdentity:
    _require_config()
    saved = await _consume_state(redis, state)
    id_token = await _exchange_code(code, saved["code_verifier"])
    claims = await _verify_id_token(redis, id_token, saved["nonce"])
    try:
        telegram_id = int(claims["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=403, detail="telegram_login_token_invalid"
        ) from exc
    if telegram_id <= 0:
        raise HTTPException(status_code=403, detail="telegram_login_token_invalid")
    return TelegramOidcIdentity(
        telegram_id=telegram_id,
        profile={
            "username": claims.get("preferred_username"),
            "first_name": claims.get("given_name") or claims.get("name"),
            "last_name": claims.get("family_name"),
            "photo_url": claims.get("picture"),
        },
        return_to=saved["return_to"],
    )
