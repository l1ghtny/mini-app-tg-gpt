import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api import document_helpers
from app.db.models import DocumentProviderArtifact, UserDocument


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16"])
async def test_srt_is_indexed_as_utf8_text_without_changing_the_document(
    monkeypatch, tmp_path, encoding
):
    subtitle = "1\n00:00:01,000 --> 00:00:03,000\nПривет, world!\n"
    source = tmp_path / "captions.SRT"
    source.write_bytes(subtitle.encode(encoding))
    uploaded = []

    async def upload_and_poll(*, vector_store_id, file):
        assert vector_store_id == "vs-srt"
        uploaded.append((file.name, file.read_text(encoding="utf-8")))
        return SimpleNamespace(id="file-srt", status="completed")

    create = AsyncMock(return_value=SimpleNamespace(id="vs-srt"))
    monkeypatch.setattr(
        document_helpers,
        "_openai_client",
        SimpleNamespace(
            vector_stores=SimpleNamespace(
                create=create,
                files=SimpleNamespace(upload_and_poll=upload_and_poll),
            )
        ),
    )
    document = UserDocument(id=uuid.uuid4(), user_id=uuid.uuid4(), filename="captions.SRT")
    artifact = DocumentProviderArtifact(document_id=document.id, status="processing")

    document_helpers._validate_extension(document.filename)
    await document_helpers._ingest_openai_artifact(
        document=document, artifact=artifact, tmp_path=str(source)
    )

    assert uploaded == [("captions.txt", subtitle)]
    assert artifact.status == "ready"
    assert artifact.external_file_id == "file-srt"
    assert source.read_bytes() == subtitle.encode(encoding)
    assert not (tmp_path / "captions.txt").exists()


@pytest.mark.asyncio
async def test_invalid_srt_text_fails_before_provider_upload(monkeypatch, tmp_path):
    source = tmp_path / "captions.srt"
    source.write_bytes(b"\xff\x80")
    create = AsyncMock()
    monkeypatch.setattr(
        document_helpers,
        "_openai_client",
        SimpleNamespace(vector_stores=SimpleNamespace(create=create)),
    )
    document = UserDocument(id=uuid.uuid4(), user_id=uuid.uuid4(), filename="captions.srt")
    artifact = DocumentProviderArtifact(document_id=document.id, status="processing")

    with pytest.raises(ValueError, match="UTF-8 or UTF-16"):
        await document_helpers._ingest_openai_artifact(
            document=document, artifact=artifact, tmp_path=str(source)
        )

    create.assert_not_awaited()
    assert not (tmp_path / "captions.txt").exists()
