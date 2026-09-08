import os
import uuid
from datetime import datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import (
    ARRAY,
    JSON,
    MetaData,
    String,
    Table,
    Column,
    Uuid,
    CheckConstraint,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.dependencies import get_current_user
from app.api.history_management import router
from app.db.database import get_session
from app.db.models import (
    AppUser,
    ChatFolder,
    Conversation,
    Message,
    MessageContent,
    MessageActivityEvent,
    RequestLedger,
    ChatFolderDocument,
    UserDocument,
    ImageAsset,
)


@pytest.fixture
async def history_db(monkeypatch):
    for key, value in {
        "R2_BUCKET": "unit-test",
        "R2_ENDPOINT": "https://storage.invalid",
        "R2_ACCESS_KEY_ID": "unit-test",
        "R2_SECRET_ACCESS_KEY": "unit-test",
    }.items():
        monkeypatch.setenv(key, value)
    database_url = os.getenv("TEST_DATABASE_URL")
    if database_url:
        engine = create_async_engine(database_url)
    else:
        # The optional local driver never touches an external database. CI uses
        # the normal PostgreSQL test database and its unmodified schema.
        pytest.importorskip("aiosqlite")
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")

        @event.listens_for(engine.sync_engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        metadata = MetaData()
        for source in SQLModel.metadata.sorted_tables:
            copy = source.to_metadata(metadata)
            copy.indexes.clear()
            for constraint in list(copy.constraints):
                if isinstance(constraint, CheckConstraint):
                    copy.constraints.remove(constraint)
            for col in copy.columns:
                col.server_default = None
                if isinstance(col.type, (JSONB, ARRAY)):
                    col.type = JSON()
        async with engine.begin() as connection:
            await connection.run_sync(metadata.create_all)

    # Production deploys this table via migrations without importing Work ORM.
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda conn: Table(
                "work_run",
                MetaData(),
                Column("id", Uuid, primary_key=True),
                Column("user_id", Uuid),
                Column("conversation_id", Uuid),
                Column("folder_id", Uuid),
                Column("status", String),
            ).create(conn, checkfirst=True)
        )

    async with AsyncSession(engine, expire_on_commit=False) as session:
        owner, other = AppUser(telegram_id=9400101), AppUser(telegram_id=9400102)
        session.add_all([owner, other])
        await session.flush()
        project = ChatFolder(user_id=owner.id, name="Project")
        session.add(project)
        await session.flush()
        document = UserDocument(user_id=owner.id, filename="notes.txt", status="ready")
        session.add(document)
        await session.flush()
        session.add(ChatFolderDocument(folder_id=project.id, document_id=document.id))
        outside = Conversation(user_id=owner.id, title="Outside")
        favorite = Conversation(user_id=owner.id, title="Favorite", is_favorite=True)
        inside = Conversation(user_id=owner.id, title="Inside", folder_id=project.id)
        foreign = Conversation(user_id=other.id, title="Secret")
        session.add_all([outside, favorite, inside, foreign])
        await session.flush()
        message = Message(conversation_id=inside.id, role="assistant")
        session.add(message)
        await session.flush()
        session.add(MessageContent(message_id=message.id, type="text", value="Answer"))
        image = MessageContent(
            message_id=message.id,
            type="image_url",
            value="https://storage.invalid/image.png",
        )
        session.add(image)
        await session.flush()
        session.add(
            ImageAsset(
                user_id=owner.id,
                conversation_id=inside.id,
                message_content_id=image.id,
                bucket="unit-test",
                key="image.png",
                public_url="https://storage.invalid/image.png",
            )
        )
        session.add(
            RequestLedger(
                user_id=owner.id,
                conversation_id=inside.id,
                assistant_message_id=message.id,
                request_id="completed",
                model_name="gpt-5.4-nano",
                feature="text",
                state="consumed",
            )
        )
        await session.commit()

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: owner

    async def db_session():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = db_session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield (
            engine,
            client,
            owner,
            outside,
            favorite,
            inside,
            foreign,
            project,
            message,
        )
    await engine.dispose()


async def preview(client):
    response = await client.get("/api/v1/history/manage")
    assert response.status_code == 200, response.text
    return response.json()


async def delete(client, conversations=None, projects=None):
    return await client.post(
        "/api/v1/history/bulk-delete",
        json={
            "conversations": conversations or [],
            "projects": projects or [],
        },
    )


def chat_selection(chat):
    return {
        "id": str(chat.id),
        "folder_id": str(chat.folder_id) if chat.folder_id else None,
    }


async def test_preview_and_selective_delete_preserve_unselected_history(history_db):
    engine, client, owner, outside, favorite, inside, foreign, project, _ = history_db
    data = await preview(client)
    assert {c["id"] for c in data["conversations"]} == {
        str(c.id) for c in [outside, favorite, inside]
    }
    assert data["projects"][0]["conversation_ids"] == [str(inside.id)]
    response = await delete(client, [chat_selection(outside), chat_selection(inside)])
    assert response.status_code == 200, response.text
    async with AsyncSession(engine) as session:
        assert await session.get(Conversation, outside.id) is None
        assert await session.get(Conversation, inside.id) is None
        assert await session.get(Conversation, favorite.id)
        assert len((await session.exec(select(UserDocument))).all()) == 1
        assert len((await session.exec(select(ChatFolderDocument))).all()) == 1
        assert await session.get(Conversation, foreign.id)
        assert await session.get(ChatFolder, project.id)
        assert len((await session.exec(select(RequestLedger))).all()) == 1
        assert not (await session.exec(select(MessageContent))).all()
        asset = (await session.exec(select(ImageAsset))).one()
        assert asset.conversation_id is None
        assert asset.message_content_id is None


async def test_project_deletion_is_explicit_and_retry_safe(history_db):
    engine, client, _, outside, favorite, inside, _, project, _ = history_db
    selected = (await preview(client))["projects"]
    response = await delete(client, projects=selected)
    assert response.status_code == 200, response.text
    assert response.json()["deleted_conversation_ids"] == [str(inside.id)]
    assert (await delete(client, projects=selected)).status_code == 200
    async with AsyncSession(engine) as session:
        assert await session.get(ChatFolder, project.id) is None
        assert len((await session.exec(select(UserDocument))).all()) == 1
        assert not (await session.exec(select(ChatFolderDocument))).all()
        assert await session.get(Conversation, outside.id)
        assert await session.get(Conversation, favorite.id)


async def test_cross_owner_selection_rejects_entire_batch(history_db):
    engine, client, _, outside, _, _, foreign, _, _ = history_db
    response = await delete(client, [chat_selection(outside), chat_selection(foreign)])
    assert response.status_code == 404
    async with AsyncSession(engine) as session:
        assert await session.get(Conversation, outside.id)
        assert await session.get(Conversation, foreign.id)


async def test_changed_membership_requires_fresh_confirmation(history_db):
    engine, client, _, outside, _, inside, _, project, _ = history_db
    selected = (await preview(client))["projects"]
    async with AsyncSession(engine) as session:
        chat = await session.get(Conversation, outside.id)
        chat.folder_id = project.id
        await session.commit()
    response = await delete(client, projects=selected)
    assert response.status_code == 409
    response = await delete(client, [chat_selection(outside)])
    assert response.status_code == 409
    async with AsyncSession(engine) as session:
        assert await session.get(Conversation, inside.id)


@pytest.mark.parametrize("busy_source", ["ledger", "activity", "work"])
async def test_busy_project_is_skipped_as_a_whole(history_db, busy_source):
    engine, client, owner, outside, _, inside, _, project, message = history_db
    selected = (await preview(client))["projects"]
    async with AsyncSession(engine) as session:
        if busy_source == "ledger":
            session.add(
                RequestLedger(
                    user_id=owner.id,
                    conversation_id=inside.id,
                    request_id="active",
                    model_name="gpt-5.4-nano",
                    feature="text",
                )
            )
        elif busy_source == "activity":
            session.add(
                MessageActivityEvent(
                    message_id=message.id,
                    sequence=0,
                    event_key="turn",
                    kind="turn",
                    status="active",
                )
            )
        else:
            work_table = await session.run_sync(
                lambda s: Table(
                    "work_run",
                    MetaData(),
                    autoload_with=s.connection(),
                )
            )
            for name in ("id", "user_id", "conversation_id", "folder_id"):
                work_table.c[name].type = Uuid()
            values = {
                "id": uuid.uuid4(),
                "user_id": owner.id,
                "conversation_id": inside.id,
                "folder_id": project.id,
                "status": "waiting_for_user",
                "stage": "waiting_for_user",
                "kind": "document_summary",
                "kind_version": 1,
                "client_request_id": str(uuid.uuid4()),
                "workflow_id": str(uuid.uuid4()),
                "input_manifest": {},
                "options": {},
                "reserved_units": 1,
                "estimated_cost_usd": 0,
                "actual_cost_usd": 0,
                "attempt_count": 0,
                "created_at": datetime.now(),
                "updated_at": datetime.now(),
            }
            await session.execute(
                work_table.insert().values(
                    **{k: v for k, v in values.items() if k in work_table.c}
                )
            )
        await session.commit()
    data = await preview(client)
    assert data["projects"][0]["busy"] is True
    response = await delete(client, [chat_selection(outside)], selected)
    assert response.status_code == 200, response.text
    assert response.json()["skipped_project_ids"] == [str(project.id)]
    async with AsyncSession(engine) as session:
        assert await session.get(Conversation, outside.id) is None
        assert await session.get(Conversation, inside.id)
        assert await session.get(ChatFolder, project.id)


async def test_empty_and_duplicate_selection_rejected(history_db):
    _, client, _, outside, *_ = history_db
    assert (await delete(client)).status_code == 422
    assert (await delete(client, [chat_selection(outside)] * 2)).status_code == 422


async def test_commit_failure_rolls_back_entire_deletion(history_db, monkeypatch):
    engine, client, _, outside, _, inside, _, project, _ = history_db
    selected = (await preview(client))["projects"]

    async def fail_commit(_):
        raise RuntimeError("simulated commit failure")

    monkeypatch.setattr(AsyncSession, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="simulated commit failure"):
        await delete(client, [chat_selection(outside)], selected)
    async with AsyncSession(engine) as session:
        assert await session.get(Conversation, outside.id)
        assert await session.get(Conversation, inside.id)
        assert await session.get(ChatFolder, project.id)


async def test_assistant_placeholder_and_reservation_share_a_commit(
    history_db, monkeypatch
):
    engine, _, owner, outside, *_ = history_db
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test")
    from app.api.chat_helpers import _create_assistant_message
    from app.services.subscription_check.entitlements import reserve_request

    async with AsyncSession(engine, expire_on_commit=False) as session:
        assistant = await _create_assistant_message(session, outside.id)
        assistant_id = assistant.id
        await session.rollback()
        assert await session.get(Message, assistant_id) is None
        assistant = await _create_assistant_message(session, outside.id)
        assistant_id = assistant.id
        ledger = await reserve_request(
            session,
            user_id=owner.id,
            conversation_id=outside.id,
            assistant_message_id=assistant.id,
            request_id="atomic",
            model_name="gpt-5.4-nano",
            feature="text",
            cost=1,
        )
        assert ledger is not None
    async with AsyncSession(engine) as session:
        assert await session.get(Message, assistant_id)
        assert (
            await session.exec(
                select(RequestLedger).where(RequestLedger.request_id == "atomic")
            )
        ).one()
