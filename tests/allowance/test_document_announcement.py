from importlib import import_module
from io import StringIO
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.db.models import WhatsNewItem
from sqlalchemy import text
import pytest

@pytest.mark.asyncio
async def test_document_notice_idempotent_localized_and_scoped(db):
    engine, _, _ = db
    migration = import_module('migrations.versions.xw0e1f2a3b50_announce_document_selection')
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

def test_document_notice_offline_sql():
    migration = import_module('migrations.versions.xw0e1f2a3b50_announce_document_selection')
    output=StringIO()
    context=MigrationContext.configure(dialect_name='postgresql', opts={'as_sql': True, 'literal_binds': True, 'output_buffer': output})
    with Operations.context(context):
        migration.upgrade()
    sql=output.getvalue().lower()
    assert 'on conflict (id) do nothing' in sql
    assert 'delete' not in sql and 'drop' not in sql
    assert migration.down_revision=='xw0e1f2a3b4f'

@pytest.mark.asyncio
@pytest.mark.parametrize('module', ['xw0e1f2a3b51_document_indexing_notice', 'xw0e1f2a3b52_document_filename_notice'])
async def test_indexing_notice_extends_existing_item_without_republishing(db, module):
    engine, _, _ = db
    migration = import_module('migrations.versions.' + module)
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: WhatsNewItem.__table__.create(c))
        def apply(c, action):
            with Operations.context(MigrationContext.configure(c)):
                action()
        original = import_module('migrations.versions.xw0e1f2a3b50_announce_document_selection')
        await conn.run_sync(lambda c: apply(c, original.upgrade))
        await conn.run_sync(lambda c: apply(c, migration.previous.upgrade))
        published = (await conn.execute(text('select published_at from whats_new_item'))).scalar_one()
        await conn.execute(WhatsNewItem.__table__.insert().values(**WhatsNewItem(id="unrelated", kind="improvement", title_en="Other", title_ru="Other", body_en="Keep", body_ru="Keep").model_dump()))
        await conn.run_sync(lambda c: apply(c, migration.upgrade))
        await conn.run_sync(lambda c: apply(c, migration.upgrade))
        row = (await conn.execute(text('select body_en, body_ru, published_at from whats_new_item where id=:id'), {'id':migration.ITEM_ID})).one()
        assert row.body_en == migration.BODY_EN
        assert row.body_ru == migration.BODY_RU
        assert row.published_at == published
        assert (await conn.execute(text('select count(*) from whats_new_item'))).scalar_one() == 2
        await conn.run_sync(lambda c: apply(c, migration.downgrade))
        assert (await conn.execute(text('select body_en from whats_new_item where id=:id'), {'id':migration.ITEM_ID})).scalar_one() == migration.previous.BODY_EN
        assert (await conn.execute(text("select body_en from whats_new_item where id='unrelated'"))).scalar_one() == 'Keep'


@pytest.mark.parametrize('module, parent', [('xw0e1f2a3b51_document_indexing_notice','xw0e1f2a3b50'), ('xw0e1f2a3b52_document_filename_notice','xw0e1f2a3b51')])
def test_indexing_notice_offline_sql(module, parent):
    migration = import_module('migrations.versions.' + module)
    output = StringIO()
    context = MigrationContext.configure(dialect_name='postgresql', opts={'as_sql':True,'literal_binds':True,'output_buffer':output})
    with Operations.context(context):
        migration.upgrade()
    sql = output.getvalue().lower()
    assert 'update whats_new_item' in sql
    assert "where id = '2026-09-24-document-selection'" in sql
    assert 'published_at' not in sql and 'insert' not in sql and 'delete' not in sql
    assert migration.down_revision == parent
