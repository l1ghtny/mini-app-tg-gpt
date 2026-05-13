"""add openai chaining fields to conversation

Revision ID: a1b2c3d4e5f6
Revises: f2b1c2d3e4f5
Create Date: 2026-05-13 10:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f2b1c2d3e4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("conversation", sa.Column("last_openai_response_id", sa.String(), nullable=True))
    op.add_column("conversation", sa.Column("openai_chain_updated_at", sa.DateTime(), nullable=True))
    op.create_index(
        op.f("ix_conversation_last_openai_response_id"),
        "conversation",
        ["last_openai_response_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_conversation_last_openai_response_id"), table_name="conversation")
    op.drop_column("conversation", "openai_chain_updated_at")
    op.drop_column("conversation", "last_openai_response_id")
