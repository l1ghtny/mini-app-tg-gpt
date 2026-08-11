from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from jose import JWTError, jwt

from app.core.config import settings


ArtifactDisposition = Literal["attachment", "inline"]
ARTIFACT_DELIVERY_TTL_SECONDS = 900
_TOKEN_KIND = "artifact_delivery"
_RANGE_PATTERN = re.compile(r"^bytes=(\d*)-(\d*)$")


class InvalidArtifactDeliveryToken(ValueError):
    pass


class InvalidArtifactRange(ValueError):
    pass


@dataclass(frozen=True)
class ArtifactDeliveryGrant:
    artifact_id: uuid.UUID
    user_id: uuid.UUID
    disposition: ArtifactDisposition


@dataclass(frozen=True)
class ArtifactByteRange:
    start: int
    end: int
    total_size: int

    @property
    def request_header(self) -> str:
        return f"bytes={self.start}-{self.end}"

    @property
    def content_range(self) -> str:
        return f"bytes {self.start}-{self.end}/{self.total_size}"


def create_artifact_delivery_token(
    *,
    artifact_id: uuid.UUID,
    user_id: uuid.UUID,
    disposition: ArtifactDisposition,
    expires_in: int = ARTIFACT_DELIVERY_TTL_SECONDS,
) -> str:
    if disposition not in {"attachment", "inline"}:
        raise ValueError("invalid artifact content disposition")
    if not settings.SECRET_KEY:
        raise RuntimeError("artifact delivery signing is not configured")
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "kind": _TOKEN_KIND,
            "artifact_id": str(artifact_id),
            "user_id": str(user_id),
            "disposition": disposition,
            "iat": now,
            "exp": now + timedelta(seconds=expires_in),
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def decode_artifact_delivery_token(
    token: str,
    *,
    expected_artifact_id: uuid.UUID,
) -> ArtifactDeliveryGrant:
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
        if payload.get("kind") != _TOKEN_KIND or "exp" not in payload:
            raise InvalidArtifactDeliveryToken("invalid artifact delivery token")
        artifact_id = uuid.UUID(payload["artifact_id"])
        user_id = uuid.UUID(payload["user_id"])
        disposition = payload["disposition"]
        if artifact_id != expected_artifact_id:
            raise InvalidArtifactDeliveryToken("artifact delivery token mismatch")
        if disposition not in {"attachment", "inline"}:
            raise InvalidArtifactDeliveryToken("invalid artifact disposition")
    except (JWTError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, InvalidArtifactDeliveryToken):
            raise
        raise InvalidArtifactDeliveryToken("invalid artifact delivery token") from exc
    return ArtifactDeliveryGrant(
        artifact_id=artifact_id,
        user_id=user_id,
        disposition=disposition,
    )


def artifact_content_disposition(
    filename: str,
    disposition: ArtifactDisposition,
) -> str:
    source = Path(filename).name.replace("\r", "").replace("\n", "") or "artifact"
    ascii_filename = (
        "".join(
            character
            for character in source
            if character.isascii()
            and (character.isalnum() or character in "._-")
        ).strip(".")
        or "artifact"
    )
    encoded_filename = quote(source, safe="")
    return (
        f'{disposition}; filename="{ascii_filename}"; '
        f"filename*=UTF-8''{encoded_filename}"
    )


def parse_artifact_range(
    value: str | None,
    *,
    total_size: int,
) -> ArtifactByteRange | None:
    if value is None:
        return None
    match = _RANGE_PATTERN.fullmatch(value.strip())
    if match is None or total_size <= 0:
        raise InvalidArtifactRange("invalid artifact range")
    raw_start, raw_end = match.groups()
    if not raw_start and not raw_end:
        raise InvalidArtifactRange("invalid artifact range")
    if raw_start:
        start = int(raw_start)
        end = int(raw_end) if raw_end else total_size - 1
    else:
        suffix_length = int(raw_end)
        if suffix_length <= 0:
            raise InvalidArtifactRange("invalid artifact range")
        start = max(total_size - suffix_length, 0)
        end = total_size - 1
    if start < 0 or start >= total_size or end < start:
        raise InvalidArtifactRange("artifact range is not satisfiable")
    return ArtifactByteRange(
        start=start,
        end=min(end, total_size - 1),
        total_size=total_size,
    )
