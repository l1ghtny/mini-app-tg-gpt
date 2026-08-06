from importlib import import_module
from io import StringIO

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext


def test_work_run_migration_is_additive_and_expands_ledger_feature(monkeypatch) -> None:
    migration = import_module(
        "migrations.versions.vc1d2e3f4a5b_add_work_runs_and_artifacts"
    )
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    monkeypatch.setattr(migration, "op", Operations(context))

    migration.upgrade()

    sql = output.getvalue().lower()
    for table in (
        "work_run_policy",
        "work_run",
        "provider_operation",
        "artifact",
        "artifact_source",
    ):
        assert f"create table {table}" in sql
    assert "alter table request_ledger add constraint ck_request_feature" in sql
    assert "'work'" in sql
    assert "drop table" not in sql
    assert "drop column" not in sql
    assert "alter column" not in sql


def test_work_run_policy_seed_has_bounded_beta_defaults(monkeypatch) -> None:
    migration = import_module(
        "migrations.versions.vc1d2e3f4a5b_add_work_runs_and_artifacts"
    )
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    monkeypatch.setattr(migration, "op", Operations(context))

    migration.upgrade()

    sql = " ".join(output.getvalue().lower().split())
    assert "'offer_comparison_xlsx'" in sql
    assert "true, 1, 25, 1.000000, 10.000000, 2" in sql
