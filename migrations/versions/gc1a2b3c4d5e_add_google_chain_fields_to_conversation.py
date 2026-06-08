"""add google chain fields to conversation

Adds google_chain_updated_at and google_chain_context_fingerprint to the
conversation table, mirroring the existing OpenAI chaining columns.

Revision ID: gc1a2b3c4d5e
Revises: ab20240523
Create Date: 2026-05-23 16:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "gc1a2b3c4d5e"
down_revision: Union[str, None] = "ab20240523"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversation",
        sa.Column("google_chain_updated_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "conversation",
        sa.Column("google_chain_context_fingerprint", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversation", "google_chain_context_fingerprint")
    op.drop_column("conversation", "google_chain_updated_at")
