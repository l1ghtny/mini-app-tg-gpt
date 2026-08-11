from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api import document_helpers


class _Session:
    def __init__(self, document) -> None:
        self.document = document

    async def get(self, model, document_id):
        return self.document if self.document and self.document.id == document_id else None


@pytest.mark.asyncio
async def test_document_source_download_is_owned_and_short_lived(monkeypatch) -> None:
    user_id = uuid.uuid4()
    document_id = uuid.uuid4()
    document = SimpleNamespace(
        id=document_id,
        user_id=user_id,
        deleted_at=None,
        source_storage_status="stored",
        source_bucket="private-documents",
        source_storage_key=f"documents/{user_id}/{document_id}/source.pdf",
        filename="private brief.pdf",
        mime_type="application/pdf",
    )
    presign = AsyncMock(return_value="https://private.example/signed-source")
    monkeypatch.setattr(document_helpers, "presign_document_source", presign)

    result = await document_helpers.get_document_source_download(
        _Session(document),  # type: ignore[arg-type]
        SimpleNamespace(id=user_id),
        document_id,
    )

    assert result.url == "https://private.example/signed-source"
    assert result.expires_in == 900
    presign.assert_awaited_once_with(
        bucket="private-documents",
        key=document.source_storage_key,
        filename="private brief.pdf",
        content_type="application/pdf",
        expires=900,
    )


@pytest.mark.asyncio
async def test_document_source_download_does_not_reveal_other_users_files() -> None:
    document = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        deleted_at=None,
        source_storage_status="stored",
        source_bucket="private-documents",
        source_storage_key="documents/private/source.pdf",
    )

    with pytest.raises(HTTPException) as error:
        await document_helpers.get_document_source_download(
            _Session(document),  # type: ignore[arg-type]
            SimpleNamespace(id=uuid.uuid4()),
            document.id,
        )

    assert error.value.status_code == 404
    assert error.value.detail == {"error": "document_not_found"}
