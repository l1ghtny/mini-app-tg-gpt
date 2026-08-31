from __future__ import annotations

from importlib import import_module
from io import StringIO
from pathlib import Path

from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory


def _render_upgrade(module_name: str) -> tuple[object, str]:
    migration = import_module(module_name)
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    migration.op = Operations(context)
    migration.upgrade()
    return migration, output.getvalue().lower()


def test_shared_database_graph_contains_the_beta_revisions() -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_current_head() == "xk8e9f0a1b2c"
    assert scripts.get_revision("xe2f3a4b5c6d").down_revision == "vc1d2e3f4a5b"
    assert scripts.get_revision("xf3a4b5c6d7e").down_revision == "xe2f3a4b5c6d"
    assert scripts.get_revision("xg4b5c6d7e8").down_revision == "xf3a4b5c6d7e"
    assert scripts.get_revision("xh5c6d7e8f9").down_revision == "xg4b5c6d7e8"
    assert scripts.get_revision("xi6d7e8f9a0b").down_revision == "xh5c6d7e8f9"
    assert scripts.get_revision("xj7e8f9a0b1c").down_revision == "xi6d7e8f9a0b"
    assert scripts.get_revision("xk8e9f0a1b2c").down_revision == "xj7e8f9a0b1c"


def test_agentic_work_migration_is_forward_compatible() -> None:
    migration, sql = _render_upgrade(
        "migrations.versions.xe2f3a4b5c6d_add_agentic_work_threads"
    )

    assert migration.down_revision == "vc1d2e3f4a5b"
    for table in (
        "work_thread",
        "work_thread_message",
        "work_plan",
        "work_thread_run",
    ):
        assert f"create table {table}" in sql
    assert "drop table" not in sql
    assert "drop column" not in sql


def test_multiple_artifact_migration_only_relaxes_the_old_constraint() -> None:
    migration, sql = _render_upgrade(
        "migrations.versions.xf3a4b5c6d7e_allow_multiple_work_artifacts"
    )

    assert migration.down_revision == "xe2f3a4b5c6d"
    assert "drop constraint uq_artifact_run_version" in sql
    assert "create index ix_artifact_work_run_version" in sql
    assert "drop table" not in sql
    assert "drop column" not in sql


def test_work_activity_migration_is_additive() -> None:
    migration, sql = _render_upgrade(
        "migrations.versions.xg4b5c6d7e8_add_work_run_activity_events"
    )

    assert migration.down_revision == "xf3a4b5c6d7e"
    assert "create table work_run_activity_event" in sql
    assert "foreign key(work_run_id) references work_run (id) on delete cascade" in sql
    assert "alter table" not in sql
    assert "drop table" not in sql
    assert "drop column" not in sql


def test_human_input_migration_is_additive() -> None:
    migration, sql = _render_upgrade(
        "migrations.versions.xh5c6d7e8f9_add_work_human_input_requests"
    )

    assert migration.down_revision == "xg4b5c6d7e8"
    assert "create table work_human_input_request" in sql
    assert "alter table" not in sql
    assert "drop table" not in sql


def test_tier_work_allowance_migration_is_additive() -> None:
    migration, sql = _render_upgrade(
        "migrations.versions.xi6d7e8f9a0b_add_tier_work_allowance"
    )

    assert migration.down_revision == "xh5c6d7e8f9"
    assert "add column monthly_work_runs" in sql
    assert "monthly_work_runs = 250" in sql
    assert "lower(name) = 'smooth tier'" in sql
    assert "drop column" not in sql


def test_chat_activity_migration_is_additive() -> None:
    migration, sql = _render_upgrade(
        "migrations.versions.xk8e9f0a1b2c_add_message_activity_events"
    )

    assert migration.down_revision == "xj7e8f9a0b1c"
    assert "create table message_activity_event" in sql
    assert "foreign key(message_id) references message (id) on delete cascade" in sql
    assert "alter table" not in sql
    assert "drop table" not in sql


def test_beta_pipeline_checks_the_shared_head_before_deploying() -> None:
    pipeline = (
        Path(__file__).resolve().parents[1]
        / "ops/teamcity/lightny-beta-pipeline.yaml"
    ).read_text(encoding="utf-8")

    assert "verify_shared_schema:" in pipeline
    assert 'export MIGRATION_MODE="check"' in pipeline
    assert "      - verify_shared_schema" in pipeline
