import importlib
import uuid

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from app.db.database import engine


@pytest.mark.asyncio
async def test_quote_migration_roundtrip_preserves_legacy_accounting_rows():
    migration=importlib.import_module("migrations.versions.xw0e1f2a3b69_customer_usage_quote")
    schema="quote_migration_"+uuid.uuid4().hex
    async with engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        await connection.execute(text("CREATE TABLE allowance_request (id INTEGER PRIMARY KEY, charged BIGINT NOT NULL)"))
        await connection.execute(text("INSERT INTO allowance_request VALUES (1, 12)"))
        def upgrade(sync_connection):
            with Operations.context(MigrationContext.configure(sync_connection)):
                migration.upgrade()
        await connection.run_sync(upgrade)
        assert (await connection.execute(text("SELECT charged, customer_quote FROM allowance_request"))).one()==(12,None)
        await connection.execute(text("UPDATE allowance_request SET customer_quote = CAST(:quote AS JSON)"), {"quote": '{"granted_units":1000}'})
        def downgrade(sync_connection):
            with Operations.context(MigrationContext.configure(sync_connection)):
                migration.downgrade()
        await connection.run_sync(downgrade)
        assert (await connection.execute(text("SELECT * FROM allowance_request"))).one()==(1,12)
        await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
