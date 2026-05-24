"""add thinking defaults to user and conversation

Revision ID: t1a2b3c4d5e6
Revises: m1a2b3c4d5e6
Create Date: 2026-05-24 16:20:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "t1a2b3c4d5e6"
down_revision = "m1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "app_user",
        sa.Column("default_thinking", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "conversation",
        sa.Column("thinking", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("conversation", "thinking")
    op.drop_column("app_user", "default_thinking")
