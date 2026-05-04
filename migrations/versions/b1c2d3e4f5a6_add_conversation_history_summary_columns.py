"""add conversation history summary columns

Revision ID: b1c2d3e4f5a6
Revises: ab55b2c9d1f0
Create Date: 2026-05-04 07:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, Sequence[str], None] = "ab55b2c9d1f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _column_exists(inspector, "conversation", "history_summary"):
        op.add_column("conversation", sa.Column("history_summary", sa.Text(), nullable=True))

    inspector = sa.inspect(bind)
    if not _column_exists(inspector, "conversation", "history_summary_up_to_message_id"):
        op.add_column(
            "conversation",
            sa.Column("history_summary_up_to_message_id", sa.Uuid(), nullable=True),
        )

    inspector = sa.inspect(bind)
    if not _column_exists(inspector, "conversation", "history_summary_updated_at"):
        op.add_column(
            "conversation",
            sa.Column("history_summary_updated_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _column_exists(inspector, "conversation", "history_summary_updated_at"):
        op.drop_column("conversation", "history_summary_updated_at")

    inspector = sa.inspect(bind)
    if _column_exists(inspector, "conversation", "history_summary_up_to_message_id"):
        op.drop_column("conversation", "history_summary_up_to_message_id")

    inspector = sa.inspect(bind)
    if _column_exists(inspector, "conversation", "history_summary"):
        op.drop_column("conversation", "history_summary")
