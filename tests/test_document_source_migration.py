from importlib import import_module
from io import StringIO

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext


def test_document_source_columns_are_additive_and_nullable(monkeypatch) -> None:
    migration = import_module(
        "migrations.versions.vb1c2d3e4f5a_add_document_source_storage"
    )
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    monkeypatch.setattr(migration, "op", Operations(context))

    migration.upgrade()

    sql = output.getvalue().lower()
    assert sql.count("alter table user_document add column") == 4
    assert "source_bucket varchar" in sql
    assert "source_storage_key varchar" in sql
    assert "source_storage_status varchar" in sql
    assert "source_stored_at timestamp without time zone" in sql
    assert "not null" not in sql
    assert " default " not in sql
