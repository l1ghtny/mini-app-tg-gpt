from importlib import import_module
from io import StringIO
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.db.models import WhatsNewItem
from sqlalchemy import text
import pytest

@pytest.mark.asyncio
async def test_startup_notice_idempotent_localized_and_scoped(db):
    engine, _, _ = db
    migration = import_module('migrations.versions.xw0e1f2a3b53_announce_startup_loading')
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: WhatsNewItem.__table__.create(c))
        def apply(c, action):
            with Operations.context(MigrationContext.configure(c)):
                action()
        await conn.run_sync(lambda c: apply(c, migration.upgrade))
        await conn.run_sync(lambda c: apply(c, migration.upgrade))
        rows=(await conn.execute(text('select id,title_en,title_ru,body_en,body_ru from whats_new_item'))).all()
        assert len(rows)==1 and rows[0][0]==migration.ITEM_ID
        assert all(rows[0])
        await conn.run_sync(lambda c: apply(c, migration.downgrade))
        assert (await conn.execute(text('select count(*) from whats_new_item'))).scalar_one()==0

def test_startup_notice_offline_sql():
    migration = import_module('migrations.versions.xw0e1f2a3b53_announce_startup_loading')
    output=StringIO()
    context=MigrationContext.configure(dialect_name='postgresql', opts={'as_sql': True, 'literal_binds': True, 'output_buffer': output})
    with Operations.context(context):
        migration.upgrade()
    sql=output.getvalue().lower()
    assert 'on conflict (id) do nothing' in sql
    assert 'delete' not in sql and 'drop' not in sql
    assert migration.down_revision=='xw0e1f2a3b52'
