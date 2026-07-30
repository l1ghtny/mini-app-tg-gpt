import json
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Request

from app.api.passkey_helpers import (
    _consume_ceremony,
    begin_passkey_authentication,
    resolve_passkey_context,
)
from app.core.config import settings


def _request(origin: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/passkeys/authentication/options",
            "headers": [(b"origin", origin.encode())],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 443),
            "scheme": "https",
        }
    )


def test_passkey_context_requires_allowed_origin_and_matching_rp(monkeypatch):
    monkeypatch.setattr(
        settings, "PASSKEY_ALLOWED_ORIGINS", ("https://app.lightny.ru",)
    )
    monkeypatch.setattr(settings, "PASSKEY_RP_ID", "lightny.ru")

    assert resolve_passkey_context(_request("https://app.lightny.ru")) == (
        "https://app.lightny.ru",
        "lightny.ru",
    )

    with pytest.raises(HTTPException) as denied:
        resolve_passkey_context(_request("https://attacker.example"))
    assert denied.value.status_code == 403

    monkeypatch.setattr(settings, "PASSKEY_RP_ID", "other.example")
    with pytest.raises(HTTPException) as mismatch:
        resolve_passkey_context(_request("https://app.lightny.ru"))
    assert mismatch.value.status_code == 503


@pytest.mark.asyncio
async def test_discoverable_passkey_challenge_is_one_time(monkeypatch):
    monkeypatch.setattr(settings, "PASSKEY_CHALLENGE_TTL_SECONDS", 300)
    redis = AsyncMock()
    redis.set.return_value = True

    ceremony_id, options = await begin_passkey_authentication(
        redis,
        origin="https://app.lightny.ru",
        rp_id="lightny.ru",
    )

    assert len(ceremony_id) >= 20
    assert options["rpId"] == "lightny.ru"
    assert options["userVerification"] == "required"
    assert options.get("allowCredentials", []) == []
    stored = json.loads(redis.set.await_args.args[1])
    assert stored["origin"] == "https://app.lightny.ru"
    assert stored["rp_id"] == "lightny.ru"
    assert stored["user_id"] is None
    assert redis.set.await_args.kwargs == {"ex": 300, "nx": True}

    redis.getdel.side_effect = [json.dumps(stored), None]
    consumed = await _consume_ceremony(
        redis,
        kind="authentication",
        ceremony_id=ceremony_id,
    )
    assert consumed == stored

    with pytest.raises(HTTPException) as replay:
        await _consume_ceremony(
            redis,
            kind="authentication",
            ceremony_id=ceremony_id,
        )
    assert replay.value.status_code == 400
