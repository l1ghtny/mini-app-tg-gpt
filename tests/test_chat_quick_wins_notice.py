"""Render the production-only notice through Alembic's PostgreSQL offline path."""
from importlib import import_module
from io import StringIO

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

MIGRATION = "migrations.versions.xw0e1f2a3b61_announce_chat_quick_wins"


def render(action):
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "literal_binds": True, "output_buffer": output},
    )
    with Operations.context(context):
        action()
    return output.getvalue()


def test_notice_is_one_active_idempotent_item_with_no_unrelated_cta():
    migration = import_module(MIGRATION)
    sql = render(migration.upgrade)
    assert migration.ITEM_ID == "2026-09-30-chat-quick-wins"
    assert "ON CONFLICT (id) DO NOTHING" in sql
    assert "'[]'::jsonb, NULL, false, NULL, NULL" in sql
    assert "INSERT INTO whats_new_item" in sql
    assert "'wrench', NULL, NULL, NULL, NULL, NULL" in sql
    assert ", true," in sql
    assert "same account" in migration.BODY_EN
    assert "в том же аккаунте" in migration.BODY_RU
    assert "later messages" in migration.BODY_EN
    assert "какая модель" in migration.BODY_RU


def test_downgrade_removes_only_the_announcement():
    migration = import_module(MIGRATION)
    assert render(migration.downgrade).strip() == (
        "DELETE FROM whats_new_item WHERE id = '2026-09-30-chat-quick-wins';"
    )


def test_notice_extends_the_single_head():
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == ["xw0e1f2a3b61"]
    assert scripts.get_revision("xw0e1f2a3b61").down_revision == "xw0e1f2a3b60"
