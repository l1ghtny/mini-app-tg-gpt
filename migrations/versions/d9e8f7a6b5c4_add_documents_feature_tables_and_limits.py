"""add documents feature tables and limits

Revision ID: d9e8f7a6b5c4
Revises: c3d4e5f6a7b8
Create Date: 2026-05-18 17:40:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "d9e8f7a6b5c4"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("subscription_tier", sa.Column("max_active_docs", sa.Integer(), nullable=False, server_default="0"))
    op.add_column(
        "subscription_tier",
        sa.Column("max_storage_bytes", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "subscription_tier",
        sa.Column("max_file_size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column("subscription_tier", sa.Column("max_pinned_docs", sa.Integer(), nullable=False, server_default="0"))
    op.add_column(
        "subscription_tier",
        sa.Column("doc_retention_hours", sa.Integer(), nullable=False, server_default="24"),
    )

    op.create_table(
        "user_document",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("mime_type", sa.String(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("usage_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="uploading"),
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_used_in_search", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("openai_file_id", sa.String(), nullable=True),
        sa.Column("openai_vector_store_id", sa.String(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_user_document_user_id"), "user_document", ["user_id"], unique=False)
    op.create_index(op.f("ix_user_document_sha256"), "user_document", ["sha256"], unique=False)
    op.create_index(op.f("ix_user_document_status"), "user_document", ["status"], unique=False)
    op.create_index(op.f("ix_user_document_is_pinned"), "user_document", ["is_pinned"], unique=False)
    op.create_index(
        op.f("ix_user_document_last_used_in_search"),
        "user_document",
        ["last_used_in_search"],
        unique=False,
    )
    op.create_index(op.f("ix_user_document_expires_at"), "user_document", ["expires_at"], unique=False)
    op.create_index(op.f("ix_user_document_openai_file_id"), "user_document", ["openai_file_id"], unique=False)
    op.create_index(
        op.f("ix_user_document_openai_vector_store_id"),
        "user_document",
        ["openai_vector_store_id"],
        unique=False,
    )
    op.create_index(op.f("ix_user_document_created_at"), "user_document", ["created_at"], unique=False)
    op.create_index(op.f("ix_user_document_deleted_at"), "user_document", ["deleted_at"], unique=False)

    op.create_table(
        "conversation_document",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attached_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversation.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["user_document.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", "document_id", name="uq_conversation_document"),
    )
    op.create_index(
        op.f("ix_conversation_document_conversation_id"),
        "conversation_document",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_conversation_document_document_id"),
        "conversation_document",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_conversation_document_attached_at"),
        "conversation_document",
        ["attached_at"],
        unique=False,
    )

    # Seed defaults for known tier names.
    op.execute(
        """
        UPDATE subscription_tier
        SET max_active_docs = 2,
            max_storage_bytes = 10485760,
            max_file_size_bytes = 5242880,
            max_pinned_docs = 0,
            doc_retention_hours = 24
        WHERE lower(name) IN ('welcoming bonus', 'welcoming_bonus', 'free');
        """
    )
    op.execute(
        """
        UPDATE subscription_tier
        SET max_active_docs = 50,
            max_storage_bytes = 209715200,
            max_file_size_bytes = 104857600,
            max_pinned_docs = 25,
            doc_retention_hours = 120
        WHERE lower(name) = 'basic';
        """
    )
    op.execute(
        """
        UPDATE subscription_tier
        SET max_active_docs = 100,
            max_storage_bytes = 524288000,
            max_file_size_bytes = 262144000,
            max_pinned_docs = 50,
            doc_retention_hours = 120
        WHERE lower(name) IN ('advanced', 'katush tier', 'close friends tier', 'smooth tier');
        """
    )
    op.execute(
        """
        UPDATE subscription_tier
        SET max_active_docs = 200,
            max_storage_bytes = 1073741824,
            max_file_size_bytes = 536870912,
            max_pinned_docs = 100,
            doc_retention_hours = 120
        WHERE lower(name) IN ('premium', 'pro');
        """
    )

    # Drop server defaults after backfill.
    op.alter_column("subscription_tier", "max_active_docs", server_default=None)
    op.alter_column("subscription_tier", "max_storage_bytes", server_default=None)
    op.alter_column("subscription_tier", "max_file_size_bytes", server_default=None)
    op.alter_column("subscription_tier", "max_pinned_docs", server_default=None)
    op.alter_column("subscription_tier", "doc_retention_hours", server_default=None)


def downgrade() -> None:
    op.drop_index(op.f("ix_conversation_document_attached_at"), table_name="conversation_document")
    op.drop_index(op.f("ix_conversation_document_document_id"), table_name="conversation_document")
    op.drop_index(op.f("ix_conversation_document_conversation_id"), table_name="conversation_document")
    op.drop_table("conversation_document")

    op.drop_index(op.f("ix_user_document_deleted_at"), table_name="user_document")
    op.drop_index(op.f("ix_user_document_created_at"), table_name="user_document")
    op.drop_index(op.f("ix_user_document_openai_vector_store_id"), table_name="user_document")
    op.drop_index(op.f("ix_user_document_openai_file_id"), table_name="user_document")
    op.drop_index(op.f("ix_user_document_expires_at"), table_name="user_document")
    op.drop_index(op.f("ix_user_document_last_used_in_search"), table_name="user_document")
    op.drop_index(op.f("ix_user_document_is_pinned"), table_name="user_document")
    op.drop_index(op.f("ix_user_document_status"), table_name="user_document")
    op.drop_index(op.f("ix_user_document_sha256"), table_name="user_document")
    op.drop_index(op.f("ix_user_document_user_id"), table_name="user_document")
    op.drop_table("user_document")

    op.drop_column("subscription_tier", "doc_retention_hours")
    op.drop_column("subscription_tier", "max_pinned_docs")
    op.drop_column("subscription_tier", "max_file_size_bytes")
    op.drop_column("subscription_tier", "max_storage_bytes")
    op.drop_column("subscription_tier", "max_active_docs")
