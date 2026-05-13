"""move personalization to dedicated table

Revision ID: d4c3b2a1908f
Revises: c9d8e7f6a5b4
Create Date: 2026-05-08 21:15:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "d4c3b2a1908f"
down_revision: Union[str, Sequence[str], None] = "c9d8e7f6a5b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {col["name"] for col in inspector.get_columns(table_name)}


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, "user_personalization"):
        op.create_table(
            "user_personalization",
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("app_user.id"), primary_key=True, nullable=False),
            sa.Column("answers", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("dismissed_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )

    inspector = sa.inspect(bind)
    has_answers = _column_exists(inspector, "app_user", "personalization_answers")
    has_completed = _column_exists(inspector, "app_user", "personalization_completed_at")
    has_dismissed = _column_exists(inspector, "app_user", "personalization_dismissed_at")
    has_updated = _column_exists(inspector, "app_user", "personalization_updated_at")

    if has_answers and has_completed and has_dismissed and has_updated:
        op.execute(
            """
            INSERT INTO user_personalization (user_id, answers, completed_at, dismissed_at, updated_at)
            SELECT id, personalization_answers, personalization_completed_at, personalization_dismissed_at, personalization_updated_at
            FROM app_user
            WHERE personalization_answers IS NOT NULL
               OR personalization_completed_at IS NOT NULL
               OR personalization_dismissed_at IS NOT NULL
               OR personalization_updated_at IS NOT NULL
            ON CONFLICT (user_id) DO UPDATE
            SET answers = EXCLUDED.answers,
                completed_at = EXCLUDED.completed_at,
                dismissed_at = EXCLUDED.dismissed_at,
                updated_at = EXCLUDED.updated_at
            """
        )

    inspector = sa.inspect(bind)
    if _column_exists(inspector, "app_user", "personalization_answers"):
        op.drop_column("app_user", "personalization_answers")
    inspector = sa.inspect(bind)
    if _column_exists(inspector, "app_user", "personalization_completed_at"):
        op.drop_column("app_user", "personalization_completed_at")
    inspector = sa.inspect(bind)
    if _column_exists(inspector, "app_user", "personalization_dismissed_at"):
        op.drop_column("app_user", "personalization_dismissed_at")
    inspector = sa.inspect(bind)
    if _column_exists(inspector, "app_user", "personalization_updated_at"):
        op.drop_column("app_user", "personalization_updated_at")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _column_exists(inspector, "app_user", "personalization_answers"):
        op.add_column("app_user", sa.Column("personalization_answers", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    inspector = sa.inspect(bind)
    if not _column_exists(inspector, "app_user", "personalization_completed_at"):
        op.add_column("app_user", sa.Column("personalization_completed_at", sa.DateTime(), nullable=True))
    inspector = sa.inspect(bind)
    if not _column_exists(inspector, "app_user", "personalization_dismissed_at"):
        op.add_column("app_user", sa.Column("personalization_dismissed_at", sa.DateTime(), nullable=True))
    inspector = sa.inspect(bind)
    if not _column_exists(inspector, "app_user", "personalization_updated_at"):
        op.add_column("app_user", sa.Column("personalization_updated_at", sa.DateTime(), nullable=True))

    if _table_exists(inspector, "user_personalization"):
        op.execute(
            """
            UPDATE app_user AS u
            SET personalization_answers = p.answers,
                personalization_completed_at = p.completed_at,
                personalization_dismissed_at = p.dismissed_at,
                personalization_updated_at = p.updated_at
            FROM user_personalization AS p
            WHERE p.user_id = u.id
            """
        )
        op.drop_table("user_personalization")
