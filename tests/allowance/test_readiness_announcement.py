from importlib import import_module
from io import StringIO
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.db.models import WhatsNewItem
from sqlalchemy import text
import pytest

migration = import_module('migrations.versions.xw0e1f2a3b66_announce_file_library')

@pytest.mark.asyncio
async def test_readiness_notice_is_idempotent_localized_and_scoped(db):
    engine, _, _ = db
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: WhatsNewItem.__table__.create(c))
        await conn.execute(WhatsNewItem.__table__.insert().values(**WhatsNewItem(id="unrelated", kind="improvement", title_en="Other", title_ru="Other", body_en="Keep", body_ru="Keep").model_dump()))
        def apply(c, action):
            with Operations.context(MigrationContext.configure(c)):
                action()
        await conn.run_sync(lambda c: apply(c, migration.upgrade))
        await conn.run_sync(lambda c: apply(c, migration.upgrade))
        row = (await conn.execute(text('select title_en,title_ru,body_en,body_ru,is_active from whats_new_item where id=:id'), {'id':migration.ITEM_ID})).one()
        assert all(row) and row.body_en == migration.BODY_EN and row.body_ru == migration.BODY_RU
        assert (await conn.execute(text('select count(*) from whats_new_item'))).scalar_one() == 2
        await conn.run_sync(lambda c: apply(c, migration.downgrade))
        assert (await conn.execute(text('select id from whats_new_item'))).scalar_one() == 'unrelated'

def test_readiness_notice_offline_sql_and_parent():
    output = StringIO()
    context = MigrationContext.configure(dialect_name='postgresql', opts={'as_sql':True,'literal_binds':True,'output_buffer':output})
    with Operations.context(context):
        migration.upgrade()
    sql = output.getvalue().lower()
    assert 'on conflict (id) do nothing' in sql and 'drop table' not in sql and 'delete from' not in sql
    assert migration.down_revision == 'xw0e1f2a3b65'
