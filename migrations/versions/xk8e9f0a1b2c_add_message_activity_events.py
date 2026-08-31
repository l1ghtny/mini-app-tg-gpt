"""add durable chat message activity events

Revision ID: xk8e9f0a1b2c
Revises: xj7e8f9a0b1c
Create Date: 2026-08-31 13:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "xk8e9f0a1b2c"
down_revision: Union[str, Sequence[str], None] = "xj7e8f9a0b1c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "message_activity_event",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_key", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["message.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "message_id",
            "sequence",
            name="uq_message_activity_sequence",
        ),
        sa.UniqueConstraint(
            "message_id",
            "event_key",
            name="uq_message_activity_event_key",
        ),
    )
    op.create_index(
        "ix_message_activity_event_message_id",
        "message_activity_event",
        ["message_id"],
    )
    op.create_index(
        "ix_message_activity_event_created_at",
        "message_activity_event",
        ["created_at"],
    )
    op.create_index(
        "ix_message_activity_message_sequence",
        "message_activity_event",
        ["message_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_message_activity_message_sequence",
        table_name="message_activity_event",
    )
    op.drop_index(
        "ix_message_activity_event_created_at",
        table_name="message_activity_event",
    )
    op.drop_index(
        "ix_message_activity_event_message_id",
        table_name="message_activity_event",
    )
    op.drop_table("message_activity_event")
