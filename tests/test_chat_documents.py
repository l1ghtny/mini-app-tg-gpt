import json
import uuid
from datetime import timedelta
from types import SimpleNamespace
import pytest
from fastapi import HTTPException, Response
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from app.db.database import engine
from app.db.models import AppUser, ChatDocument, Conversation
from app.schemas.chat_documents import DocumentSpec
from app.services import chat_documents
from app.api.chat_documents import download_document


@pytest.fixture
def private_objects(monkeypatch):
    objects = {}
    monkeypatch.setattr(chat_documents.settings, "CHAT_DOCUMENT_GENERATION_ENABLED", True)
    monkeypatch.setattr(chat_documents.storage, "get_private_documents_bucket", lambda:"private-test")
    async def upload(**kwargs):
        from pathlib import Path
        objects[kwargs["key"]] = Path(kwargs["path"]).read_bytes()
    async def delete(**kwargs):
        objects.pop(kwargs["key"], None)
    monkeypatch.setattr(chat_documents.storage, "upload_document_source", upload)
    monkeypatch.setattr(chat_documents.storage, "delete_document_source", delete)
    return objects


async def account():
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=int(uuid.uuid4().int % 10**12), first_name="Synthetic")
        session.add(user)
        await session.flush()
        conversation = Conversation(user_id=user.id, title="Documents")
        session.add(conversation)
        await session.commit()
        return user, SimpleNamespace(user_id=user.id, conversation_id=conversation.id, request_id=str(uuid.uuid4()), document_read_ranges={})


def arguments(**updates):
    return {"query":"Create a launch document", "format":"docx", "parent_document_id":None,
        "title":"План запуска", "blocks":[{"kind":"paragraph", "text":"Платежи и документы", "items":[], "rows":[]}], **updates}


@pytest.mark.asyncio
async def test_publication_is_idempotent_and_revision_requires_full_owned_source(private_objects):
    user, run = await account()
    first = await chat_documents.create_chat_document(run, arguments())
    assert await chat_documents.create_chat_document(run, arguments()) == first
    assert len(private_objects) == 1
    changed = arguments(title="План запуска после проверки", parent_document_id=first["id"])
    with pytest.raises(ValueError, match="complete source"):
        await chat_documents.create_chat_document(run, changed)
    source = json.loads(await chat_documents.read_chat_document(run, first["id"]))
    assert source["next_offset"] is None
    assert DocumentSpec.model_validate_json(source["content_chunk"]).title == "План запуска"
    second = await chat_documents.create_chat_document(run, changed)
    assert second["parent_id"] == first["id"] and second["version"] == 2
    assert len(private_objects) == 2
    _, stranger = await account()
    with pytest.raises(HTTPException) as error:
        await chat_documents.read_chat_document(stranger, first["id"])
    assert error.value.status_code == 404
    async with AsyncSession(engine) as session:
        assert (await session.exec(select(ChatDocument))).all()[0].spec["title"] == "План запуска"


@pytest.mark.asyncio
async def test_chunked_reads_are_complete_bounded_and_cannot_skip_source(private_objects):
    _, run = await account()
    args = arguments(blocks=[{"kind":"paragraph", "text":f"Раздел {i} "+"Полный исходный текст. "*80, "items":[], "rows":[]} for i in range(8)])
    file = await chat_documents.create_chat_document(run, args)
    from app.services.shared_chat_loop import result_tokens
    chunks = []
    offset = 0
    while offset is not None:
        value = await chat_documents.read_chat_document(run, file["id"], offset)
        assert result_tokens(value) <= 2000
        result = json.loads(value)
        chunks.append(result["content_chunk"])
        offset = result["next_offset"]
    assert len(chunks) > 1
    assert json.loads("".join(chunks))["blocks"] == args["blocks"]
    assert (await chat_documents.create_chat_document(run, {**args,"parent_document_id":file["id"]}))["version"] == 2


@pytest.mark.asyncio
async def test_download_is_owner_scoped_no_store_and_expires(private_objects, monkeypatch):
    user, run = await account()
    file = await chat_documents.create_chat_document(run, arguments())
    async def sign(**kwargs):
        assert kwargs["disposition"] == "attachment" and kwargs["expires"] == 300
        return "https://storage.example.invalid/signed-download"
    monkeypatch.setattr("app.api.chat_documents.presign_document_source", sign)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        response = Response()
        result = await download_document(uuid.UUID(file["id"]), response, user, session)
        assert response.headers["Cache-Control"] == "private, no-store"
        assert set(result) == {"url", "filename"}
        stranger, _ = await account()
        with pytest.raises(HTTPException) as error:
            await download_document(uuid.UUID(file["id"]), Response(), stranger, session)
        assert error.value.status_code == 404
        row = await session.get(ChatDocument, uuid.UUID(file["id"]))
        row.expires_at = chat_documents.now() - timedelta(seconds=1)
        await session.commit()
        with pytest.raises(HTTPException) as error:
            await download_document(row.id, Response(), user, session)
        assert error.value.status_code == 410
        assert await chat_documents.delete_chat_documents(session) == 1
        await session.commit()
        assert row.status == "deleted" and row.spec == {} and not private_objects


@pytest.mark.asyncio
async def test_failed_upload_keeps_exact_cleanup_key_and_never_delivers(private_objects, monkeypatch):
    _, run = await account()
    async def ambiguous_upload(**kwargs):
        private_objects[kwargs["key"]] = b"upstream may have received data"
        raise TimeoutError()
    monkeypatch.setattr(chat_documents.storage, "upload_document_source", ambiguous_upload)
    with pytest.raises(TimeoutError):
        await chat_documents.create_chat_document(run, arguments())
    async with AsyncSession(engine, expire_on_commit=False) as session:
        row = (await session.exec(select(ChatDocument))).one()
        assert row.status == "failed" and row.key in private_objects
        assert await chat_documents.delete_chat_documents(session) == 1
        await session.commit()
        assert not private_objects


@pytest.mark.asyncio
async def test_account_export_and_deletion_cover_generated_content(private_objects):
    from fastapi import BackgroundTasks
    from app.api.account import export_account_data, delete_account, DeleteAccountRequest
    user, run = await account()
    await chat_documents.create_chat_document(run, arguments())
    async with AsyncSession(engine, expire_on_commit=False) as session:
        export = json.loads((await export_account_data(user, session)).body)
        item = export["generated_documents"][0]
        assert item["spec"]["title"] == "План запуска"
        assert not {"bucket", "key", "request_key"} & item.keys()
        await delete_account(DeleteAccountRequest(confirmation="DELETE"), BackgroundTasks(), user, session)
        assert not private_objects
        row = (await session.exec(select(ChatDocument))).one()
        assert row.spec == {} and row.status == "deleted"


@pytest.mark.asyncio
async def test_cleanup_failure_cannot_starve_other_expired_files(private_objects, monkeypatch):
    _, run = await account()
    first = await chat_documents.create_chat_document(run, arguments())
    second = await chat_documents.create_chat_document(run, arguments(title="Second"))
    original = chat_documents.storage.delete_document_source
    async with AsyncSession(engine, expire_on_commit=False) as session:
        a = await session.get(ChatDocument, uuid.UUID(first["id"]))
        b = await session.get(ChatDocument, uuid.UUID(second["id"]))
        a.expires_at = chat_documents.now() - timedelta(days=2)
        b.expires_at = chat_documents.now() - timedelta(days=1)
        await session.commit()
        async def fail_first(**kwargs):
            if kwargs["key"] == a.key:
                raise TimeoutError("Synthetic failure")
            await original(**kwargs)
        monkeypatch.setattr(chat_documents.storage, "delete_document_source", fail_first)
        assert await chat_documents.delete_chat_documents(session, batch_size=1) == 0
        await session.commit()
        assert await chat_documents.delete_chat_documents(session, batch_size=1) == 1
        await session.commit()
        assert a.status == "ready" and a.cleanup_attempted_at and b.status == "deleted"


@pytest.mark.asyncio
async def test_document_migration_roundtrip_leaves_payment_history_intact():
    from importlib import import_module
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text
    from app.db.models import Payment
    user, _ = await account()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        payment = Payment(user_id=user.id, tier_name="Synthetic", amount=49000, confirmation_applied=True, tbank_status="CONFIRMED")
        session.add(payment)
        await session.commit()
        payment_id = payment.id
    migration = import_module("migrations.versions.xw0e1f2a3b72_chat_documents")
    def apply(connection, action):
        with Operations.context(MigrationContext.configure(connection)):
            action()
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: apply(c, migration.downgrade))
        await conn.run_sync(lambda c: apply(c, migration.upgrade))
        assert (await conn.execute(text("select confirmation_applied,amount from payment where id=:id"), {"id":payment_id})).one() == (True, 49000)
        assert (await conn.execute(text("select count(*) from information_schema.columns where table_schema='public' and table_name='chat_document' and column_name='cleanup_attempted_at'"))).scalar_one() == 1
