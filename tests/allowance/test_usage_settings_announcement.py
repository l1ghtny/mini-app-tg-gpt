from importlib import import_module
from io import StringIO
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
import pytest
from app.db.models import WhatsNewItem

migration = import_module("migrations.versions.xw0e1f2a3b70_announce_usage_settings")

@pytest.mark.asyncio
async def test_production_notice_is_idempotent_and_scoped(db):
    engine, _, _ = db
    async with engine.begin() as connection:
        await connection.run_sync(lambda c: WhatsNewItem.__table__.create(c))
        await connection.execute(WhatsNewItem.__table__.insert().values(**WhatsNewItem(id="unrelated",kind="fix",title_en="Keep",title_ru="Keep",body_en="Keep",body_ru="Keep").model_dump()))
        def apply(c, action):
            with Operations.context(MigrationContext.configure(c)):
                action()
        await connection.run_sync(lambda c:apply(c,migration.upgrade))
        await connection.run_sync(lambda c:apply(c,migration.upgrade))
        assert (await connection.execute(text("SELECT count(*) FROM whats_new_item"))).scalar_one() == 2
        row = (await connection.execute(text("SELECT kind,is_active,title_en FROM whats_new_item WHERE id=:id"),{"id":migration.ITEM_ID})).one()
        assert row == ("improvement",True,migration.TITLE_EN)
        await connection.run_sync(lambda c:apply(c,migration.downgrade))
        assert (await connection.execute(text("SELECT id FROM whats_new_item"))).scalar_one() == "unrelated"

def test_notice_supports_offline_migration_and_does_not_publish_drafts():
    output=StringIO()
    context=MigrationContext.configure(dialect_name="postgresql",opts={"as_sql":True,"literal_binds":True,"output_buffer":output})
    with Operations.context(context):migration.upgrade()
    sql=output.getvalue().lower()
    assert "on conflict (id) do nothing" in sql
    assert migration.down_revision == "xw0e1f2a3b69"
    assert "draft" not in sql and "delete from" not in sql
