from importlib import import_module
from io import StringIO

from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import text

from app.db.models import WhatsNewItem

migration = import_module("migrations.versions.xw0e1f2a3b68_announce_image_completion")


@pytest.mark.asyncio
async def test_image_completion_notice_is_idempotent_and_scoped(db):
    engine, _, _ = db
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: WhatsNewItem.__table__.create(c))
        await conn.execute(WhatsNewItem.__table__.insert().values(**WhatsNewItem(
            id="unrelated", kind="improvement", title_en="Other", title_ru="Other",
            body_en="Keep", body_ru="Keep",
        ).model_dump()))

        def apply(c, action):
            with Operations.context(MigrationContext.configure(c)):
                action()

        await conn.run_sync(lambda c: apply(c, migration.upgrade))
        await conn.run_sync(lambda c: apply(c, migration.upgrade))
        row = (await conn.execute(text(
            "SELECT title_en,title_ru,body_en,body_ru,is_active,kind "
            "FROM whats_new_item WHERE id=:id"
        ), {"id": migration.ITEM_ID})).one()
        assert row.title_en == migration.TITLE_EN and row.title_ru == migration.TITLE_RU
        assert row.body_en == migration.BODY_EN and row.body_ru == migration.BODY_RU
        assert row.is_active and row.kind == "fix"
        assert (await conn.execute(text("SELECT count(*) FROM whats_new_item"))).scalar_one() == 2
        await conn.run_sync(lambda c: apply(c, migration.downgrade))
        assert (await conn.execute(text("SELECT id FROM whats_new_item"))).scalar_one() == "unrelated"


def test_image_completion_notice_offline_sql_and_single_head():
    output = StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={
        "as_sql": True, "literal_binds": True, "output_buffer": output,
    })
    with Operations.context(context):
        migration.upgrade()
    sql = output.getvalue().lower()
    assert "on conflict (id) do nothing" in sql
    assert "drop table" not in sql and "delete from" not in sql
    assert migration.down_revision == "xw0e1f2a3b66"
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == ["xw0e1f2a3b72"]
