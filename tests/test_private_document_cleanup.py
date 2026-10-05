from unittest.mock import AsyncMock
from types import SimpleNamespace
import pytest
from app.r2 import private_documents as storage


def test_private_storage_never_uses_the_public_bucket(monkeypatch):
    monkeypatch.setattr(storage.Settings, "R2_PRIVATE_DOCUMENTS_BUCKET", storage.R2_BUCKET)
    with pytest.raises(storage.PrivateDocumentStorageConfigurationError):
        storage.get_private_documents_bucket()


@pytest.mark.asyncio
async def test_source_deletion_requires_the_configured_private_bucket(monkeypatch):
    monkeypatch.setattr(storage, "get_private_documents_bucket", lambda: "private-documents")
    with pytest.raises(storage.PrivateDocumentStorageConfigurationError):
        await storage.delete_document_source(bucket="foreign-bucket", key="source.csv")


@pytest.mark.asyncio
async def test_source_deletion_uses_private_client_without_a_public_fallback(monkeypatch):
    delete = AsyncMock()
    client = AsyncMock()
    client.__aenter__.return_value = SimpleNamespace(delete_object=delete)
    monkeypatch.setattr(storage, "get_private_documents_bucket", lambda: "private-documents")
    monkeypatch.setattr(storage, "_private_s3_client", lambda: client)
    await storage.delete_document_source(bucket="private-documents", key="source.csv")
    delete.assert_awaited_once_with(Bucket="private-documents", Key="source.csv")
