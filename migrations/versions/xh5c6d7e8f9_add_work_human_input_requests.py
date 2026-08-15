"""add durable Work human input requests

Revision ID: xh5c6d7e8f9
Revises: xg4b5c6d7e8
Create Date: 2026-08-15 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "xh5c6d7e8f9"
down_revision: Union[str, Sequence[str], None] = "xg4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "work_human_input_request",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("work_run_id", sa.Uuid(), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("question", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("answer", sa.String(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_response_id", sa.String(length=128), nullable=False),
        sa.Column("provider_call_id", sa.String(length=128), nullable=False),
        sa.Column("answer_idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("answered_at", sa.DateTime(), nullable=True),
        sa.Column("resumed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["thread_id"], ["work_thread.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["work_run_id"], ["work_run.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "round >= 1 AND round <= 2",
            name="ck_work_human_input_round",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'answered', 'resumed', 'cancelled')",
            name="ck_work_human_input_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "work_run_id",
            "round",
            name="uq_work_human_input_run_round",
        ),
        sa.UniqueConstraint(
            "provider",
            "provider_call_id",
            name="uq_work_human_input_provider_call",
        ),
    )
    op.create_index(
        "uq_work_human_input_pending_run",
        "work_human_input_request",
        ["work_run_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_work_human_input_request_thread_id",
        "work_human_input_request",
        ["thread_id"],
    )
    op.create_index(
        "ix_work_human_input_request_work_run_id",
        "work_human_input_request",
        ["work_run_id"],
    )
    op.create_index(
        "ix_work_human_input_request_status",
        "work_human_input_request",
        ["status"],
    )
    op.create_index(
        "ix_work_human_input_request_created_at",
        "work_human_input_request",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "uq_work_human_input_pending_run",
        table_name="work_human_input_request",
    )
    op.drop_index(
        "ix_work_human_input_request_created_at",
        table_name="work_human_input_request",
    )
    op.drop_index(
        "ix_work_human_input_request_status",
        table_name="work_human_input_request",
    )
    op.drop_index(
        "ix_work_human_input_request_work_run_id",
        table_name="work_human_input_request",
    )
    op.drop_index(
        "ix_work_human_input_request_thread_id",
        table_name="work_human_input_request",
    )
    op.drop_table("work_human_input_request")
