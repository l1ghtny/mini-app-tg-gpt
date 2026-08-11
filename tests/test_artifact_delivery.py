from __future__ import annotations

import uuid
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt

import app.api.work_runs as work_run_api
from app.api.dependencies import get_current_user
from app.core.config import settings
from app.db.database import get_session
from app.db.models import AppUser, Artifact
from app.services.work_runs.artifact_delivery import (
    InvalidArtifactDeliveryToken,
    InvalidArtifactRange,
    create_artifact_delivery_token,
    decode_artifact_delivery_token,
    parse_artifact_range,
)


class _Result:
    def __init__(self, value: object | None) -> None:
        self.value = value

    def first(self) -> object | None:
        return self.value


class _Session:
    def __init__(self, artifact: Artifact) -> None:
        self.artifact = artifact

    async def exec(self, _statement: object) -> _Result:
        return _Result(self.artifact)


class _ObjectStream:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.size_bytes = len(payload)
        self.closed = False

    async def iter_bytes(self):
        yield self.payload

    async def close(self) -> None:
        self.closed = True


def _artifact(user_id: uuid.UUID, *, preview_kind: str = "pdf") -> Artifact:
    return Artifact(
        work_run_id=uuid.uuid4(),
        user_id=user_id,
        kind="generated_file",
        status="ready",
        filename="Отчёт Lightny.pdf",
        mime_type="application/pdf",
        size_bytes=8,
        bucket="private-documents",
        storage_key=f"artifacts/{user_id}/report.pdf",
        artifact_metadata={"preview_kind": preview_kind},
    )


def _client(user: AppUser, artifact: Artifact) -> TestClient:
    app = FastAPI()
    app.include_router(work_run_api.work_runs, prefix="/api/v1")

    async def session_override():
        yield _Session(artifact)

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_session] = session_override
    return TestClient(app)


def test_delivery_token_is_scoped_and_not_a_bearer_access_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SECRET_KEY", "artifact-test-secret")
    artifact_id = uuid.uuid4()
    user_id = uuid.uuid4()

    token = create_artifact_delivery_token(
        artifact_id=artifact_id,
        user_id=user_id,
        disposition="inline",
    )

    claims = jwt.get_unverified_claims(token)
    assert "sub" not in claims
    assert decode_artifact_delivery_token(
        token,
        expected_artifact_id=artifact_id,
    ).user_id == user_id
    with pytest.raises(InvalidArtifactDeliveryToken):
        decode_artifact_delivery_token(token, expected_artifact_id=uuid.uuid4())


def test_expired_delivery_token_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SECRET_KEY", "artifact-test-secret")
    artifact_id = uuid.uuid4()
    token = create_artifact_delivery_token(
        artifact_id=artifact_id,
        user_id=uuid.uuid4(),
        disposition="attachment",
        expires_in=-1,
    )

    with pytest.raises(InvalidArtifactDeliveryToken):
        decode_artifact_delivery_token(token, expected_artifact_id=artifact_id)


def test_artifact_download_uses_lightny_delivery_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SECRET_KEY", "artifact-test-secret")
    user = AppUser(telegram_id=777001)
    artifact = _artifact(user.id)
    client = _client(user, artifact)

    response = client.get(f"/api/v1/artifacts/{artifact.id}/download")

    assert response.status_code == 200
    url = response.json()["url"]
    parsed = urlsplit(url)
    assert parsed.path == f"/api/v1/artifacts/{artifact.id}/content"
    assert "cloudflarestorage.com" not in url
    token = parse_qs(parsed.query)["token"][0]
    grant = decode_artifact_delivery_token(
        token,
        expected_artifact_id=artifact.id,
    )
    assert grant.user_id == user.id
    assert grant.disposition == "attachment"


def test_inline_preview_streams_through_lightny_with_range_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SECRET_KEY", "artifact-test-secret")
    user = AppUser(telegram_id=777002)
    artifact = _artifact(user.id)
    client = _client(user, artifact)
    stream = _ObjectStream(b"PDF")
    open_calls: list[dict[str, str | None]] = []

    async def open_stream(**kwargs):
        open_calls.append(kwargs)
        return stream

    monkeypatch.setattr(work_run_api, "open_artifact_stream", open_stream)

    preview_response = client.get(
        f"/api/v1/artifacts/{artifact.id}/inline-preview"
    )
    assert preview_response.status_code == 200
    preview_url = preview_response.json()["url"]
    assert "cloudflarestorage.com" not in preview_url

    content_response = client.get(preview_url, headers={"Range": "bytes=1-3"})

    assert content_response.status_code == 206
    assert content_response.content == b"PDF"
    assert content_response.headers["content-range"] == "bytes 1-3/8"
    assert content_response.headers["content-disposition"].startswith("inline;")
    assert "filename*=UTF-8''" in content_response.headers["content-disposition"]
    assert content_response.headers["referrer-policy"] == "no-referrer"
    assert open_calls == [
        {
            "bucket": "private-documents",
            "key": artifact.storage_key,
            "range_header": "bytes=1-3",
        }
    ]
    assert stream.closed is True


def test_artifact_range_parser_rejects_unsatisfiable_range() -> None:
    assert parse_artifact_range("bytes=-3", total_size=10).request_header == (
        "bytes=7-9"
    )
    with pytest.raises(InvalidArtifactRange, match="not satisfiable"):
        parse_artifact_range("bytes=10-20", total_size=10)
