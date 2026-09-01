"""add durable conversation drafts and favorites

Revision ID: xl9f0a1b2c3d
Revises: xk8e9f0a1b2c
Create Date: 2026-09-01 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "xl9f0a1b2c3d"
down_revision: Union[str, Sequence[str], None] = "xk8e9f0a1b2c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversation",
        sa.Column(
            "is_favorite",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "conversation",
        sa.Column("favorited_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "conversation",
        sa.Column("draft_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "conversation",
        sa.Column("draft_updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_conversation_user_favorite",
        "conversation",
        ["user_id", "is_favorite"],
    )


def downgrade() -> None:
    op.drop_index("ix_conversation_user_favorite", table_name="conversation")
    op.drop_column("conversation", "draft_updated_at")
    op.drop_column("conversation", "draft_text")
    op.drop_column("conversation", "favorited_at")
    op.drop_column("conversation", "is_favorite")
