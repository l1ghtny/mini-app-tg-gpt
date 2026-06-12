"""add image asset retention metadata

Revision ID: i1a2b3c4d5e6
Revises: g2a3b4c5d6e7
Create Date: 2026-06-12 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "i1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "g2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("image_asset"):
        op.create_table(
            "image_asset",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("message_content_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("bucket", sa.String(), nullable=False),
            sa.Column("key", sa.String(), nullable=False),
            sa.Column("public_url", sa.String(), nullable=False),
            sa.Column("source", sa.String(), nullable=False, server_default="generated"),
            sa.Column("retention_policy", sa.String(), nullable=False, server_default="free_30d"),
            sa.Column("status", sa.String(), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("last_checked_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["conversation_id"], ["conversation.id"]),
            sa.ForeignKeyConstraint(["message_content_id"], ["messagecontent.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["app_user.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    indexes = {idx.get("name") for idx in inspector.get_indexes("image_asset")}

    def create_index_if_missing(name: str, columns: list[str]) -> None:
        if name not in indexes:
            op.create_index(name, "image_asset", columns, unique=False)

    create_index_if_missing(op.f("ix_image_asset_user_id"), ["user_id"])
    create_index_if_missing(op.f("ix_image_asset_conversation_id"), ["conversation_id"])
    create_index_if_missing(op.f("ix_image_asset_message_content_id"), ["message_content_id"])
    create_index_if_missing(op.f("ix_image_asset_bucket"), ["bucket"])
    create_index_if_missing(op.f("ix_image_asset_key"), ["key"])
    create_index_if_missing(op.f("ix_image_asset_public_url"), ["public_url"])
    create_index_if_missing(op.f("ix_image_asset_source"), ["source"])
    create_index_if_missing(op.f("ix_image_asset_retention_policy"), ["retention_policy"])
    create_index_if_missing(op.f("ix_image_asset_status"), ["status"])
    create_index_if_missing(op.f("ix_image_asset_created_at"), ["created_at"])
    create_index_if_missing(op.f("ix_image_asset_expires_at"), ["expires_at"])
    create_index_if_missing(op.f("ix_image_asset_deleted_at"), ["deleted_at"])
    create_index_if_missing(op.f("ix_image_asset_last_checked_at"), ["last_checked_at"])
    create_index_if_missing("ix_image_asset_user_status_expires", ["user_id", "status", "expires_at"])
    create_index_if_missing("ix_image_asset_content", ["message_content_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("image_asset"):
        op.drop_table("image_asset")
