import io
import os
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.datastructures import Headers

import app.api.document_helpers as document_helpers
from app.db.models import AppUser, UserDocument
from app.schemas.documents import DocumentCapabilitiesResponse


def _capabilities() -> DocumentCapabilitiesResponse:
    return DocumentCapabilitiesResponse(
        status="active",
        tier_name=None,
        max_active_docs=2,
        active_doc_count=0,
        max_pinned_docs=0,
        pinned_doc_count=0,
        max_storage_bytes=10 * 1024 * 1024,
        used_storage_bytes=0,
        remaining_storage_bytes=10 * 1024 * 1024,
        max_file_size_bytes=5 * 1024 * 1024,
        doc_retention_hours=24,
    )


@pytest.mark.asyncio
async def test_spreadsheet_upload_persists_private_source_before_enqueue(
    monkeypatch,
    tmp_path: Path,
) -> None:
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)
    source_path = tmp_path / "offers.csv"
    source_path.write_bytes(b"name,price\nTea,100\n")

    async def fake_capabilities(session, user):
        return _capabilities()

    async def fake_persist(upload, target_filename):
        return str(source_path), source_path.stat().st_size, "sha256-1"

    async def fake_store(*, document, bucket, tmp_path):
        assert Path(tmp_path).read_bytes() == b"name,price\nTea,100\n"
        document.source_bucket = bucket
        document.source_storage_key = f"documents/{document.id}/source.csv"
        document.source_storage_status = "stored"
        document.source_stored_at = datetime(2026, 8, 6, 12, 0, 0)

    monkeypatch.setattr(document_helpers, "get_document_capabilities", fake_capabilities)
    monkeypatch.setattr(document_helpers, "_persist_upload_to_temp_file", fake_persist)
    monkeypatch.setattr(document_helpers, "_store_document_source", fake_store)
    monkeypatch.setattr(
        document_helpers,
        "get_private_documents_bucket",
        lambda: "private-documents",
    )

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=721000904)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        background_tasks = BackgroundTasks()
        upload = UploadFile(
            filename="offers.csv",
            file=io.BytesIO(b"name,price\nTea,100\n"),
            headers=Headers({"content-type": "text/csv"}),
        )

        response = await document_helpers.upload_document(
            session=session,
            user=user,
            background_tasks=background_tasks,
            upload=upload,
        )
        persisted = (
            await session.exec(select(UserDocument).where(UserDocument.id == response.id))
        ).one()

    await engine.dispose()

    assert persisted.source_bucket == "private-documents"
    assert persisted.source_storage_status == "stored"
    assert persisted.source_storage_key.endswith("/source.csv")
    assert persisted.status == "ready"
    assert not source_path.exists()
    assert background_tasks.tasks == []


@pytest.mark.asyncio
async def test_spreadsheet_upload_fails_closed_when_private_storage_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)
    source_path = tmp_path / "offers.csv"
    source_path.write_bytes(b"name,price\nTea,100\n")

    async def fake_capabilities(session, user):
        return _capabilities()

    async def fake_persist(upload, target_filename):
        return str(source_path), source_path.stat().st_size, "sha256-1"

    async def failing_store(**kwargs):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(document_helpers, "get_document_capabilities", fake_capabilities)
    monkeypatch.setattr(document_helpers, "_persist_upload_to_temp_file", fake_persist)
    monkeypatch.setattr(document_helpers, "_store_document_source", failing_store)
    monkeypatch.setattr(
        document_helpers,
        "get_private_documents_bucket",
        lambda: "private-documents",
    )

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=721000905)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        upload = UploadFile(
            filename="offers.csv",
            file=io.BytesIO(b"name,price\nTea,100\n"),
            headers=Headers({"content-type": "text/csv"}),
        )

        with pytest.raises(HTTPException) as exc_info:
            await document_helpers.upload_document(
                session=session,
                user=user,
                background_tasks=BackgroundTasks(),
                upload=upload,
            )
        documents = (await session.exec(select(UserDocument))).all()

    await engine.dispose()

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {"error": "document_source_storage_failed"}
    assert documents == []
    assert not source_path.exists()
