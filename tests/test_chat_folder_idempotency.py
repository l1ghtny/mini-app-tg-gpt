import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.chat_folder_helpers import handle_create_folder
from app.db.models import ChatFolder, ChatFolderDocument
from app.schemas.chat_folders import ChatFolderCreate


@pytest.mark.asyncio
async def test_create_folder_reuses_client_request_id():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(ChatFolder.__table__.create)
            await connection.run_sync(ChatFolderDocument.__table__.create)

        owner = SimpleNamespace(id=uuid.uuid4())
        request_id = uuid.uuid4()
        async with AsyncSession(engine, expire_on_commit=False) as session:
            first = await handle_create_folder(
                request=ChatFolderCreate(name="Research", client_request_id=request_id),
                session=session,
                current_user=owner,
            )
            repeated = await handle_create_folder(
                request=ChatFolderCreate(
                    name="Different name", client_request_id=request_id
                ),
                session=session,
                current_user=owner,
            )
            folders = (await session.exec(select(ChatFolder))).all()

        assert repeated.id == first.id
        assert repeated.name == "Research"
        assert repeated.client_request_id == request_id
        assert len(folders) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_request_id_is_scoped_to_user_and_legacy_requests_still_create():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(ChatFolder.__table__.create)
            await connection.run_sync(ChatFolderDocument.__table__.create)

        request_id = uuid.uuid4()
        async with AsyncSession(engine, expire_on_commit=False) as session:
            for user_id in (uuid.uuid4(), uuid.uuid4()):
                await handle_create_folder(
                    request=ChatFolderCreate(
                        name="Project", client_request_id=request_id
                    ),
                    session=session,
                    current_user=SimpleNamespace(id=user_id),
                )
            owner = SimpleNamespace(id=uuid.uuid4())
            await handle_create_folder(
                request=ChatFolderCreate(name="Old client"),
                session=session,
                current_user=owner,
            )
            await handle_create_folder(
                request=ChatFolderCreate(name="Old client"),
                session=session,
                current_user=owner,
            )
            folders = (await session.exec(select(ChatFolder))).all()

        assert len(folders) == 4
    finally:
        await engine.dispose()
