from importlib import import_module
from datetime import UTC, datetime

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select
from app.db.database import engine
from app.db.models import WhatsNewItem


@pytest.mark.asyncio
async def test_document_notice_stays_hidden_and_rerun_preserves_activation():
    migration = import_module("migrations.versions.xw0e1f2a3b73_stage_chat_documents_notice")
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_current_head() == migration.revision

    def apply(connection, action):
        with Operations.context(MigrationContext.configure(connection)):
            action()

    now = datetime.now(UTC).replace(tzinfo=None)
    async with engine.begin() as connection:
        await connection.execute(WhatsNewItem.__table__.insert().values(
            id="document-notice-unrelated", kind="feature", title_en="Other", title_ru="Другое",
            body_en="Keep", body_ru="Сохранить", audience_plans=[], pinned=False,
            published_at=now, is_active=True, created_at=now, updated_at=now,
        ))
        await connection.run_sync(lambda conn: apply(conn, migration.upgrade))
        row = (await connection.execute(select(WhatsNewItem.__table__).where(WhatsNewItem.id == migration.ITEM_ID))).mappings().one()
        assert row["is_active"] is False
        assert row["min_app_version"] == "2.5.0"
        await connection.execute(WhatsNewItem.__table__.update().where(WhatsNewItem.id == migration.ITEM_ID).values(is_active=True, title_en="Operator edited"))
        await connection.run_sync(lambda conn: apply(conn, migration.upgrade))
        row = (await connection.execute(select(WhatsNewItem.__table__).where(WhatsNewItem.id == migration.ITEM_ID))).mappings().one()
        assert row["is_active"] is True and row["title_en"] == "Operator edited"
        await connection.run_sync(lambda conn: apply(conn, migration.downgrade))
        rows = (await connection.execute(select(WhatsNewItem.id))).scalars().all()
        assert migration.ITEM_ID not in rows and "document-notice-unrelated" in rows
