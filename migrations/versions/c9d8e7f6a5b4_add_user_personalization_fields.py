"""add user personalization fields

Revision ID: c9d8e7f6a5b4
Revises: b8a9f7c6d5e4
Create Date: 2026-05-08 20:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "c9d8e7f6a5b4"
down_revision: Union[str, Sequence[str], None] = "b8a9f7c6d5e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _column_exists(inspector, "app_user", "personalization_answers"):
        op.add_column(
            "app_user",
            sa.Column("personalization_answers", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )

    inspector = sa.inspect(bind)
    if not _column_exists(inspector, "app_user", "personalization_completed_at"):
        op.add_column("app_user", sa.Column("personalization_completed_at", sa.DateTime(), nullable=True))

    inspector = sa.inspect(bind)
    if not _column_exists(inspector, "app_user", "personalization_dismissed_at"):
        op.add_column("app_user", sa.Column("personalization_dismissed_at", sa.DateTime(), nullable=True))

    inspector = sa.inspect(bind)
    if not _column_exists(inspector, "app_user", "personalization_updated_at"):
        op.add_column("app_user", sa.Column("personalization_updated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _column_exists(inspector, "app_user", "personalization_updated_at"):
        op.drop_column("app_user", "personalization_updated_at")

    inspector = sa.inspect(bind)
    if _column_exists(inspector, "app_user", "personalization_dismissed_at"):
        op.drop_column("app_user", "personalization_dismissed_at")

    inspector = sa.inspect(bind)
    if _column_exists(inspector, "app_user", "personalization_completed_at"):
        op.drop_column("app_user", "personalization_completed_at")

    inspector = sa.inspect(bind)
    if _column_exists(inspector, "app_user", "personalization_answers"):
        op.drop_column("app_user", "personalization_answers")
