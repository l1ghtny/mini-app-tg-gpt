"""SQL contract for the production document-download announcement."""
from importlib import import_module
from io import StringIO
import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory


@pytest.fixture(autouse=True)
def rebuild_test_db():
    # These contracts render offline SQL and never use a database.
    yield


def render(action):
    output = StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "literal_binds": True, "output_buffer": output})
    with Operations.context(context):
        action()
    return output.getvalue()


def test_download_notice_has_localized_copy_and_idempotent_insert():
    migration = import_module("migrations.versions.xw0e1f2a3b74_announce_document_downloads")
    sql = render(migration.upgrade)
    assert "ON CONFLICT (id) DO NOTHING" in sql
    assert "'2.5.1', false," in sql
    assert ", true," in sql
    assert "cancelled" in migration.BODY_EN
    assert "отменили" in migration.BODY_RU
    assert "Download in browser" in migration.BODY_EN
    assert "Скачать через браузер" in migration.BODY_RU
    assert render(migration.downgrade).strip() == "DELETE FROM whats_new_item WHERE id = '2026-10-08-document-downloads';"


def test_download_notice_extends_single_shared_head():
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    assert scripts.get_heads() == ["xw0e1f2a3b74"]
    assert scripts.get_revision("xw0e1f2a3b74").down_revision == "xw0e1f2a3b73"
