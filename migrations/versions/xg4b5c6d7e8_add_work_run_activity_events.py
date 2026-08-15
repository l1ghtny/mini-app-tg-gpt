"""add durable Work execution activity events

Revision ID: xg4b5c6d7e8
Revises: xf3a4b5c6d7e
Create Date: 2026-08-15 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "xg4b5c6d7e8"
down_revision: Union[str, Sequence[str], None] = "xf3a4b5c6d7e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "work_run_activity_event",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("work_run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_key", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=True),
        sa.Column("title", sa.String(length=240), nullable=True),
        sa.Column("detail", sa.String(length=1000), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["work_run_id"], ["work_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "work_run_id",
            "sequence",
            name="uq_work_run_activity_sequence",
        ),
        sa.UniqueConstraint(
            "work_run_id",
            "event_key",
            name="uq_work_run_activity_event_key",
        ),
    )
    op.create_index(
        "ix_work_run_activity_event_work_run_id",
        "work_run_activity_event",
        ["work_run_id"],
    )
    op.create_index(
        "ix_work_run_activity_event_kind",
        "work_run_activity_event",
        ["kind"],
    )
    op.create_index(
        "ix_work_run_activity_event_created_at",
        "work_run_activity_event",
        ["created_at"],
    )
    op.create_index(
        "ix_work_run_activity_run_sequence",
        "work_run_activity_event",
        ["work_run_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_work_run_activity_run_sequence",
        table_name="work_run_activity_event",
    )
    op.drop_index(
        "ix_work_run_activity_event_created_at",
        table_name="work_run_activity_event",
    )
    op.drop_index(
        "ix_work_run_activity_event_kind",
        table_name="work_run_activity_event",
    )
    op.drop_index(
        "ix_work_run_activity_event_work_run_id",
        table_name="work_run_activity_event",
    )
    op.drop_table("work_run_activity_event")
