"""add conversation search tables

Revision ID: cs1a2b3c4d5e
Revises: gs2a3b4c5d6e
Create Date: 2026-06-11 16:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "cs1a2b3c4d5e"
down_revision: Union[str, Sequence[str], None] = "gs2a3b4c5d6e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "conversation_search_chunk",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("message_content_id", sa.Uuid(), nullable=False),
        sa.Column("message_role", sa.String(), nullable=False),
        sa.Column("chunk_ordinal", sa.Integer(), nullable=False),
        sa.Column("chunk_text", sa.String(), nullable=False),
        sa.Column("text_hash", sa.String(), nullable=False),
        sa.Column("embedding", sa.ARRAY(sa.Float()), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversation.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["message.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_content_id"], ["messagecontent.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "message_content_id",
            "chunk_ordinal",
            name="uq_conversation_search_chunk_message_content_chunk",
        ),
    )
    op.create_index(
        "ix_conversation_search_chunk_user_id",
        "conversation_search_chunk",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_search_chunk_message_id",
        "conversation_search_chunk",
        ["message_id"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_search_chunk_message_content_id",
        "conversation_search_chunk",
        ["message_content_id"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_search_chunk_text_hash",
        "conversation_search_chunk",
        ["text_hash"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_search_chunk_message_role",
        "conversation_search_chunk",
        ["message_role"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_search_chunk_user_conversation",
        "conversation_search_chunk",
        ["user_id", "conversation_id"],
        unique=False,
    )

    op.create_table(
        "conversation_search_projection",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("projection_text", sa.String(), nullable=False),
        sa.Column("summary_source", sa.String(), nullable=False),
        sa.Column("embedding", sa.ARRAY(sa.Float()), nullable=False),
        sa.Column("last_indexed_message_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversation.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", name="uq_conversation_search_projection_conversation"),
    )
    op.create_index(
        "ix_conversation_search_projection_user_id",
        "conversation_search_projection",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_search_projection_conversation_id",
        "conversation_search_projection",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_search_projection_user_conversation",
        "conversation_search_projection",
        ["user_id", "conversation_id"],
        unique=False,
    )

    op.create_table(
        "conversation_search_job",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_type", sa.String(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("dedupe_key", sa.String(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("run_after", sa.DateTime(), nullable=False),
        sa.Column("locked_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversation.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["message.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversation_search_job_job_type", "conversation_search_job", ["job_type"], unique=False)
    op.create_index("ix_conversation_search_job_status", "conversation_search_job", ["status"], unique=False)
    op.create_index("ix_conversation_search_job_dedupe_key", "conversation_search_job", ["dedupe_key"], unique=False)
    op.create_index(
        "ix_conversation_search_job_status_run_after",
        "conversation_search_job",
        ["status", "run_after"],
        unique=False,
    )
    op.create_index("ix_conversation_search_job_message_id", "conversation_search_job", ["message_id"], unique=False)
    op.create_index(
        "ix_conversation_search_job_locked_at",
        "conversation_search_job",
        ["locked_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_conversation_search_job_locked_at", table_name="conversation_search_job")
    op.drop_index("ix_conversation_search_job_message_id", table_name="conversation_search_job")
    op.drop_index("ix_conversation_search_job_status_run_after", table_name="conversation_search_job")
    op.drop_index("ix_conversation_search_job_dedupe_key", table_name="conversation_search_job")
    op.drop_index("ix_conversation_search_job_status", table_name="conversation_search_job")
    op.drop_index("ix_conversation_search_job_job_type", table_name="conversation_search_job")
    op.drop_table("conversation_search_job")

    op.drop_index("ix_conversation_search_projection_user_conversation", table_name="conversation_search_projection")
    op.drop_index("ix_conversation_search_projection_conversation_id", table_name="conversation_search_projection")
    op.drop_index("ix_conversation_search_projection_user_id", table_name="conversation_search_projection")
    op.drop_table("conversation_search_projection")

    op.drop_index("ix_conversation_search_chunk_user_conversation", table_name="conversation_search_chunk")
    op.drop_index("ix_conversation_search_chunk_message_role", table_name="conversation_search_chunk")
    op.drop_index("ix_conversation_search_chunk_text_hash", table_name="conversation_search_chunk")
    op.drop_index("ix_conversation_search_chunk_message_content_id", table_name="conversation_search_chunk")
    op.drop_index("ix_conversation_search_chunk_message_id", table_name="conversation_search_chunk")
    op.drop_index("ix_conversation_search_chunk_user_id", table_name="conversation_search_chunk")
    op.drop_table("conversation_search_chunk")
