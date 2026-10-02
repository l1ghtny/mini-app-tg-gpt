from importlib import import_module
from io import StringIO

from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import text

from app.services import allowance

migration = import_module("migrations.versions.xw0e1f2a3b62_generation_execution_plans")


def apply(connection, action):
    with Operations.context(MigrationContext.configure(connection)):
        action()


@pytest.mark.asyncio
async def test_generation_migration_roundtrip_preserves_existing_balances(db):
    engine, session, user = db
    row = await allowance.account(session, user.id)
    await allowance.reserve(
        session,
        user_id=user.id,
        conversation_id=None,
        request_id="old-request",
        model="gpt-5.6-terra",
        ceiling=100,
    )
    await allowance.begin_attempt(
        session,
        user_id=user.id,
        request_id="old-request",
        step_key="old-step",
        model="gpt-5.6-terra",
        budget=80,
    )
    uid = row.id
    await session.close()
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: apply(c, migration.downgrade))
        await conn.run_sync(lambda c: apply(c, migration.upgrade))
        account = (
            await conn.execute(
                text(
                    "select granted,reserved,grant_policy_version from allowance_account where id=:id"
                ),
                {"id": uid},
            )
        ).one()
        assert account == (1250000, 100, "2026-09-18-v1")
        request = (
            await conn.execute(
                text(
                    "select ceiling,execution_plan,recovery_ceiling from allowance_request"
                )
            )
        ).one()
        assert request == (100, None, 0)
        assert (
            await conn.execute(
                text("select budget,recovery from allowance_provider_attempt")
            )
        ).one() == (80, False)
        await conn.run_sync(lambda c: apply(c, migration.downgrade))
        assert (
            await conn.execute(text("select reserved from allowance_account"))
        ).scalar_one() == 100
        await conn.run_sync(lambda c: apply(c, migration.upgrade))


def test_generation_migration_offline_sql_and_single_head():
    buffer = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "literal_binds": True, "output_buffer": buffer},
    )
    with Operations.context(context):
        migration.upgrade()
    sql = buffer.getvalue().lower()
    assert "add column execution_plan" in sql and "check (recovery_ceiling >= 0)" in sql
    assert "update allowance" not in sql and "whats_new" not in sql
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == [
        migration.revision
    ]
