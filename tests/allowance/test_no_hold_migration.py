from importlib import import_module
from io import StringIO

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.services import allowance

migration = import_module("migrations.versions.xw0e1f2a3b63_no_hold_task_policy")


def apply(connection, action):
    with Operations.context(MigrationContext.configure(connection)):
        action()


@pytest.mark.asyncio
async def test_additive_no_hold_migration_preserves_held_requests(db):
    engine, session, user = db
    r = await allowance.reserve(
        session,
        user_id=user.id,
        conversation_id=None,
        request_id="old-held",
        model="gpt-5.6-terra",
        ceiling=1000,
    )
    rid = r.id
    await session.close()
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: apply(c, migration.downgrade))
        assert (
            await conn.execute(text("select reserved from allowance_account"))
        ).scalar_one() == 1000
        await conn.run_sync(lambda c: apply(c, migration.upgrade))
        old = (
            await conn.execute(
                text(
                    "select admission_policy,supplier_ceiling,ceiling from allowance_request where id=:id"
                ),
                {"id": rid},
            )
        ).one()
        assert old == ("held-legacy", 0, 1000)


def test_no_hold_offline_sql_has_no_balance_rewrite_or_activation():
    buffer = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "literal_binds": True, "output_buffer": buffer},
    )
    with Operations.context(context):
        migration.upgrade()
    sql = buffer.getvalue().lower()
    assert "add column admission_policy" in sql and "held-legacy" in sql
    assert "ix_allowance_active_tasks" in sql
    assert "update allowance" not in sql and "whats_new" not in sql
