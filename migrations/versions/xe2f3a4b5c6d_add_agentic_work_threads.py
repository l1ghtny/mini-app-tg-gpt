"""add agentic Work threads and plans

Revision ID: xe2f3a4b5c6d
Revises: vc1d2e3f4a5b
Create Date: 2026-08-11 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "xe2f3a4b5c6d"
down_revision: Union[str, Sequence[str], None] = "vc1d2e3f4a5b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "work_thread",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("folder_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("goal", sa.String(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("context_manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_work_thread_user_id", "work_thread", ["user_id"])
    op.create_index("ix_work_thread_conversation_id", "work_thread", ["conversation_id"])
    op.create_index("ix_work_thread_folder_id", "work_thread", ["folder_id"])
    op.create_index("ix_work_thread_status", "work_thread", ["status"])
    op.create_index("ix_work_thread_created_at", "work_thread", ["created_at"])
    op.create_index("ix_work_thread_user_updated", "work_thread", ["user_id", "updated_at"])

    op.create_table(
        "work_thread_message",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["thread_id"], ["work_thread.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_work_thread_message_thread_id", "work_thread_message", ["thread_id"])
    op.create_index("ix_work_thread_message_created_at", "work_thread_message", ["created_at"])

    op.create_table(
        "work_plan",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("summary", sa.String(), nullable=False),
        sa.Column("execution_kind", sa.String(length=64), nullable=False),
        sa.Column("steps", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expected_outputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("assumptions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("provider_response_id", sa.String(length=128), nullable=True),
        sa.Column("usage", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("actual_cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["thread_id"], ["work_thread.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thread_id", "version", name="uq_work_plan_thread_version"),
    )
    op.create_index("ix_work_plan_thread_id", "work_plan", ["thread_id"])
    op.create_index("ix_work_plan_status", "work_plan", ["status"])
    op.create_index("ix_work_plan_created_at", "work_plan", ["created_at"])

    op.create_table(
        "work_thread_run",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("work_run_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["work_plan.id"]),
        sa.ForeignKeyConstraint(["thread_id"], ["work_thread.id"]),
        sa.ForeignKeyConstraint(["work_run_id"], ["work_run.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_work_thread_run_thread_id", "work_thread_run", ["thread_id"])
    op.create_index("ix_work_thread_run_plan_id", "work_thread_run", ["plan_id"])
    op.create_index(
        "ix_work_thread_run_work_run_id",
        "work_thread_run",
        ["work_run_id"],
        unique=True,
    )

    op.execute(
        """
        INSERT INTO work_run_policy (
            kind, enabled, max_active_per_user, monthly_allowance_per_user,
            per_run_budget_usd, global_daily_budget_usd, queue_concurrency,
            created_at, updated_at
        ) VALUES (
            'agentic_task', TRUE, 1, 25, 1.000000, 10.000000, 2,
            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
        ON CONFLICT (kind) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM work_run_policy WHERE kind = 'agentic_task'")
    op.drop_index("ix_work_thread_run_work_run_id", table_name="work_thread_run")
    op.drop_index("ix_work_thread_run_plan_id", table_name="work_thread_run")
    op.drop_index("ix_work_thread_run_thread_id", table_name="work_thread_run")
    op.drop_table("work_thread_run")
    op.drop_index("ix_work_plan_created_at", table_name="work_plan")
    op.drop_index("ix_work_plan_status", table_name="work_plan")
    op.drop_index("ix_work_plan_thread_id", table_name="work_plan")
    op.drop_table("work_plan")
    op.drop_index("ix_work_thread_message_created_at", table_name="work_thread_message")
    op.drop_index("ix_work_thread_message_thread_id", table_name="work_thread_message")
    op.drop_table("work_thread_message")
    op.drop_index("ix_work_thread_user_updated", table_name="work_thread")
    op.drop_index("ix_work_thread_created_at", table_name="work_thread")
    op.drop_index("ix_work_thread_status", table_name="work_thread")
    op.drop_index("ix_work_thread_folder_id", table_name="work_thread")
    op.drop_index("ix_work_thread_conversation_id", table_name="work_thread")
    op.drop_index("ix_work_thread_user_id", table_name="work_thread")
    op.drop_table("work_thread")
