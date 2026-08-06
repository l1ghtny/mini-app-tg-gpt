"""Add private source storage metadata to user documents.

Revision ID: vb1c2d3e4f5a
Revises: va1b2c3d4e5f
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "vb1c2d3e4f5a"
down_revision: Union[str, Sequence[str], None] = "va1b2c3d4e5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_document",
        sa.Column("source_bucket", sa.String(), nullable=True),
    )
    op.add_column(
        "user_document",
        sa.Column("source_storage_key", sa.String(), nullable=True),
    )
    op.add_column(
        "user_document",
        sa.Column("source_storage_status", sa.String(), nullable=True),
    )
    op.add_column(
        "user_document",
        sa.Column("source_stored_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_document", "source_stored_at")
    op.drop_column("user_document", "source_storage_status")
    op.drop_column("user_document", "source_storage_key")
    op.drop_column("user_document", "source_bucket")
