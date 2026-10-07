import importlib.util
import os
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import AppUser, UserIdentity


@pytest.mark.asyncio
async def test_provider_constraint_upgrade_and_safe_downgrade():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is required for migration integration")
    path = (
        Path(__file__).parents[1]
        / "migrations/versions/xw0e1f2a3b74_yandex_identity.py"
    )
    spec = importlib.util.spec_from_file_location("yandex_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_async_engine(url)

    def run(connection, action):
        migration.op = Operations(MigrationContext.configure(connection))
        action()

    async with engine.begin() as connection:
        await connection.execute(
            text("ALTER TABLE user_identity DROP CONSTRAINT ck_user_identity_provider")
        )
        await connection.execute(
            text(
                "ALTER TABLE user_identity ADD CONSTRAINT ck_user_identity_provider CHECK (provider IN ('telegram','email'))"
            )
        )
        await connection.run_sync(lambda c: run(c, migration.upgrade))
        await connection.run_sync(lambda c: run(c, migration.downgrade))
        await connection.run_sync(lambda c: run(c, migration.upgrade))
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=None)
        session.add(user)
        await session.flush()
        identity = UserIdentity(user_id=user.id, provider="yandex", subject="123")
        session.add(identity)
        await session.commit()
        identity_id = identity.id
    with pytest.raises(IntegrityError):
        async with engine.begin() as connection:
            await connection.run_sync(lambda c: run(c, migration.downgrade))
    async with AsyncSession(engine) as session:
        assert (await session.get(UserIdentity, identity_id)).subject == "123"
    await engine.dispose()
