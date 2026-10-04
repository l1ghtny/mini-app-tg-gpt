"""Offline SQL contract for the production-verified policy announcement."""
from importlib import import_module
from io import StringIO

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

MIGRATION = "migrations.versions.xw0e1f2a3b64_announce_generation_policy"


def render(action):
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "literal_binds": True, "output_buffer": output},
    )
    with Operations.context(context):
        action()
    return output.getvalue()


def test_notice_is_active_idempotent_and_has_no_unsupported_cta():
    migration = import_module(MIGRATION)
    sql = render(migration.upgrade)
    assert "ON CONFLICT (id) DO NOTHING" in sql
    assert "'[]'::jsonb, NULL, false, NULL, NULL" in sql
    assert "'wrench', NULL, NULL, NULL, NULL, NULL" in sql
    assert ", true," in sql
    assert "two tasks" in migration.BODY_EN
    assert "до двух задач" in migration.BODY_RU
    assert "resources are available" in migration.BODY_EN
    assert "если хватит ресурсов" in migration.BODY_RU


def test_downgrade_only_removes_this_notice():
    migration = import_module(MIGRATION)
    assert render(migration.downgrade).strip() == (
        "DELETE FROM whats_new_item WHERE id = '2026-10-04-generation-policy';"
    )


def test_extends_single_shared_head():
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    assert scripts.get_heads() == ["xw0e1f2a3b64"]
    assert scripts.get_revision("xw0e1f2a3b64").down_revision == "xw0e1f2a3b63"
