"""The audio notice is staged privately and changes no other announcement."""

import os
from datetime import UTC, datetime
from importlib import import_module
from types import SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import insert, select
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.models import WhatsNewItem


MIGRATION = "migrations.versions.xw0e1f2a3b56_stage_audio_transcription_notice"


@pytest.mark.asyncio
async def test_audio_notice_is_hidden_idempotent_and_downgrade_is_scoped():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("requires a disposable TEST_DATABASE_URL")
    assert "test" in (make_url(database_url).database or "").lower()

    migration = import_module(MIGRATION)
    assert migration.down_revision == "xw0e1f2a3b55"
    assert migration.ITEM_ID == "2026-09-25-audio-transcription"
    assert "Available on plans that include transcription" in migration.BODY_EN
    assert "доступна на тарифах, в которые она входит" in migration.BODY_RU
    assert "30 minutes" in migration.BODY_EN and "20 MB" in migration.BODY_EN
    assert "30 минут" in migration.BODY_RU and "20 МБ" in migration.BODY_RU

    engine = create_async_engine(database_url)
    other_id = "audio-notice-migration-other-item"
    now = datetime.now(UTC).replace(tzinfo=None)

    def apply(connection, action):
        with Operations.context(MigrationContext.configure(connection)):
            action()

    try:
        async with engine.begin() as connection:
            await connection.execute(insert(WhatsNewItem).values(
                id=other_id, kind="improvement", title_en="Other notice",
                title_ru="Другое обновление", body_en="Leave this item alone.",
                body_ru="Не менять это обновление.", icon="wrench",
                audience_plans=[], published_at=now, is_active=True,
                created_at=now, updated_at=now,
            ))
            await connection.run_sync(lambda sync: apply(sync, migration.upgrade))
            staged = SimpleNamespace(**(await connection.execute(select(WhatsNewItem.__table__).where(
                WhatsNewItem.id == migration.ITEM_ID
            ))).mappings().one())
            assert staged.kind == "feature"
            assert staged.is_active is False
            assert staged.starts_at is None
            assert staged.audience_plans == []
            assert staged.title_en == migration.TITLE_EN
            assert staged.title_ru == migration.TITLE_RU
            assert staged.body_en == migration.BODY_EN
            assert staged.body_ru == migration.BODY_RU
            assert staged.cta_label_en == "View usage"
            assert staged.cta_label_ru == "Посмотреть расход"
            assert staged.cta_kind == "open_subscription"
            assert staged.cta_value == "overview"
            published_at = staged.published_at

            # A rerun must preserve an operator's later activation and edits.
            await connection.execute(
                WhatsNewItem.__table__.update()
                .where(WhatsNewItem.id == migration.ITEM_ID)
                .values(is_active=True, title_en="Operator edited title")
            )
            await connection.run_sync(lambda sync: apply(sync, migration.upgrade))
            retained = SimpleNamespace(**(await connection.execute(select(WhatsNewItem.__table__).where(
                WhatsNewItem.id == migration.ITEM_ID
            ))).mappings().one())
            assert retained.is_active is True
            assert retained.title_en == "Operator edited title"
            assert retained.published_at == published_at
            assert len((await connection.execute(select(WhatsNewItem.__table__))).all()) == 2

            await connection.run_sync(lambda sync: apply(sync, migration.downgrade))
            assert (await connection.execute(select(WhatsNewItem.__table__.c.id).where(
                WhatsNewItem.id == migration.ITEM_ID
            ))).scalar_one_or_none() is None
            other = SimpleNamespace(**(await connection.execute(select(WhatsNewItem.__table__).where(
                WhatsNewItem.id == other_id
            ))).mappings().one())
            assert other.is_active is True
            assert other.title_en == "Other notice"
    finally:
        await engine.dispose()
