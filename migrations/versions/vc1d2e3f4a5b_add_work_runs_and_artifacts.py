"""Add durable work runs and immutable output artifacts.

Revision ID: vc1d2e3f4a5b
Revises: vb1c2d3e4f5a
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "vc1d2e3f4a5b"
down_revision: str | Sequence[str] | None = "vb1c2d3e4f5a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_request_feature", "request_ledger", type_="check")
    op.create_check_constraint(
        "ck_request_feature",
        "request_ledger",
        "feature IN ('text','image','doc','deepsearch','web_search','transcription','work')",
    )

    op.create_table(
        "work_run_policy",
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("max_active_per_user", sa.Integer(), nullable=False),
        sa.Column("monthly_allowance_per_user", sa.Integer(), nullable=False),
        sa.Column("per_run_budget_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("global_daily_budget_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("queue_concurrency", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "max_active_per_user > 0", name="ck_work_run_policy_active_positive"
        ),
        sa.CheckConstraint(
            "monthly_allowance_per_user > 0", name="ck_work_run_policy_monthly_positive"
        ),
        sa.CheckConstraint(
            "per_run_budget_usd >= 0", name="ck_work_run_policy_run_budget_nonnegative"
        ),
        sa.CheckConstraint(
            "global_daily_budget_usd >= 0",
            name="ck_work_run_policy_daily_budget_nonnegative",
        ),
        sa.CheckConstraint(
            "queue_concurrency > 0", name="ck_work_run_policy_concurrency_positive"
        ),
        sa.PrimaryKeyConstraint("kind"),
    )
    op.execute(
        sa.text(
            """
            INSERT INTO work_run_policy (
                kind,
                enabled,
                max_active_per_user,
                monthly_allowance_per_user,
                per_run_budget_usd,
                global_daily_budget_usd,
                queue_concurrency
            ) VALUES (
                'offer_comparison_xlsx',
                TRUE,
                1,
                25,
                1.000000,
                10.000000,
                2
            )
            """
        )
    )

    op.create_table(
        "work_run",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("folder_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("assistant_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("request_ledger_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("kind_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("progress_percent", sa.Integer(), nullable=True),
        sa.Column("client_request_id", sa.String(length=128), nullable=False),
        sa.Column("workflow_id", sa.String(length=128), nullable=False),
        sa.Column(
            "input_manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("reserved_units", sa.Numeric(12, 4), nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("actual_cost_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("queued_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "progress_percent IS NULL OR progress_percent BETWEEN 0 AND 100",
            name="ck_work_run_progress_range",
        ),
        sa.CheckConstraint(
            "reserved_units >= 0", name="ck_work_run_reserved_units_nonnegative"
        ),
        sa.CheckConstraint(
            "estimated_cost_usd >= 0", name="ck_work_run_estimated_cost_nonnegative"
        ),
        sa.CheckConstraint(
            "actual_cost_usd >= 0", name="ck_work_run_actual_cost_nonnegative"
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_work_run_attempt_count_nonnegative"
        ),
        sa.ForeignKeyConstraint(
            ["assistant_message_id"], ["message.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversation.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["folder_id"], ["chat_folder.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["request_ledger_id"], ["request_ledger.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "client_request_id", name="uq_work_run_user_client_request"
        ),
        sa.UniqueConstraint("workflow_id", name="uq_work_run_workflow_id"),
    )
    op.create_index("ix_work_run_user_id", "work_run", ["user_id"])
    op.create_index("ix_work_run_conversation_id", "work_run", ["conversation_id"])
    op.create_index("ix_work_run_folder_id", "work_run", ["folder_id"])
    op.create_index("ix_work_run_kind", "work_run", ["kind"])
    op.create_index("ix_work_run_status", "work_run", ["status"])
    op.create_index("ix_work_run_error_code", "work_run", ["error_code"])
    op.create_index("ix_work_run_created_at", "work_run", ["created_at"])
    op.create_index(
        "ix_work_run_queue_claim",
        "work_run",
        ["status", "lease_expires_at", "queued_at"],
    )
    op.create_index(
        "ix_work_run_user_status_created",
        "work_run",
        ["user_id", "status", "created_at"],
    )

    op.create_table(
        "provider_operation",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("work_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation_key", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("operation_kind", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider_request_id", sa.String(length=255), nullable=True),
        sa.Column("provider_response_id", sa.String(length=255), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("usage", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("actual_cost_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_provider_operation_attempt_nonnegative"
        ),
        sa.CheckConstraint(
            "estimated_cost_usd >= 0",
            name="ck_provider_operation_estimated_cost_nonnegative",
        ),
        sa.CheckConstraint(
            "actual_cost_usd >= 0", name="ck_provider_operation_actual_cost_nonnegative"
        ),
        sa.ForeignKeyConstraint(["work_run_id"], ["work_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "work_run_id", "operation_key", name="uq_provider_operation_run_key"
        ),
    )
    op.create_index(
        "ix_provider_operation_work_run_id", "provider_operation", ["work_run_id"]
    )
    op.create_index("ix_provider_operation_status", "provider_operation", ["status"])

    op.create_table(
        "artifact",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("work_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("assistant_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("folder_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("parent_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=False),
        sa.Column("bucket", sa.String(length=255), nullable=True),
        sa.Column("storage_key", sa.String(length=1024), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("version > 0", name="ck_artifact_version_positive"),
        sa.CheckConstraint("size_bytes >= 0", name="ck_artifact_size_nonnegative"),
        sa.ForeignKeyConstraint(
            ["assistant_message_id"], ["message.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversation.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["folder_id"], ["chat_folder.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["parent_artifact_id"], ["artifact.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["work_run_id"], ["work_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("work_run_id", "version", name="uq_artifact_run_version"),
        sa.UniqueConstraint("storage_key", name="uq_artifact_storage_key"),
    )
    op.create_index("ix_artifact_work_run_id", "artifact", ["work_run_id"])
    op.create_index("ix_artifact_user_id", "artifact", ["user_id"])
    op.create_index("ix_artifact_status", "artifact", ["status"])
    op.create_index("ix_artifact_created_at", "artifact", ["created_at"])

    op.create_table(
        "artifact_source",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("sheet_name", sa.String(length=255), nullable=True),
        sa.Column("row_start", sa.Integer(), nullable=True),
        sa.Column("row_end", sa.Integer(), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column(
            "provider_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "ordinal >= 0", name="ck_artifact_source_ordinal_nonnegative"
        ),
        sa.CheckConstraint(
            "row_start IS NULL OR row_start > 0",
            name="ck_artifact_source_row_start_positive",
        ),
        sa.CheckConstraint(
            "row_end IS NULL OR row_end > 0", name="ck_artifact_source_row_end_positive"
        ),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifact.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["document_id"], ["user_document.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "artifact_id", "ordinal", name="uq_artifact_source_ordinal"
        ),
    )
    op.create_index(
        "ix_artifact_source_artifact_id", "artifact_source", ["artifact_id"]
    )
    op.create_index(
        "ix_artifact_source_document_id", "artifact_source", ["document_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_artifact_source_document_id", table_name="artifact_source")
    op.drop_index("ix_artifact_source_artifact_id", table_name="artifact_source")
    op.drop_table("artifact_source")
    op.drop_index("ix_artifact_created_at", table_name="artifact")
    op.drop_index("ix_artifact_status", table_name="artifact")
    op.drop_index("ix_artifact_user_id", table_name="artifact")
    op.drop_index("ix_artifact_work_run_id", table_name="artifact")
    op.drop_table("artifact")
    op.drop_index("ix_provider_operation_status", table_name="provider_operation")
    op.drop_index("ix_provider_operation_work_run_id", table_name="provider_operation")
    op.drop_table("provider_operation")
    op.drop_index("ix_work_run_user_status_created", table_name="work_run")
    op.drop_index("ix_work_run_queue_claim", table_name="work_run")
    op.drop_index("ix_work_run_created_at", table_name="work_run")
    op.drop_index("ix_work_run_error_code", table_name="work_run")
    op.drop_index("ix_work_run_status", table_name="work_run")
    op.drop_index("ix_work_run_kind", table_name="work_run")
    op.drop_index("ix_work_run_folder_id", table_name="work_run")
    op.drop_index("ix_work_run_conversation_id", table_name="work_run")
    op.drop_index("ix_work_run_user_id", table_name="work_run")
    op.drop_table("work_run")
    op.drop_table("work_run_policy")

    op.drop_constraint("ck_request_feature", "request_ledger", type_="check")
    op.create_check_constraint(
        "ck_request_feature",
        "request_ledger",
        "feature IN ('text','image','doc','deepsearch','web_search','transcription')",
    )
