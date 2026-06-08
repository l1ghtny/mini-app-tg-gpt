"""add chat_starter_suggestion table

Revision ID: gs1a2b3c4d5e
Revises: gd2a3b4c5d6e
Create Date: 2026-05-24 12:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "gs1a2b3c4d5e"
down_revision: Union[str, None] = "gd2a3b4c5d6e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE_NAME = "chat_starter_suggestion"
_INDEXES = (
    ("ix_chat_starter_suggestion_language", ["language"]),
    ("ix_chat_starter_suggestion_is_active", ["is_active"]),
    ("ix_chat_starter_suggestion_created_at", ["created_at"]),
    ("ix_chat_starter_suggestion_active_lang", ["is_active", "language"]),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if _TABLE_NAME not in inspector.get_table_names():
        op.create_table(
            _TABLE_NAME,
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("language", sa.String(), nullable=False),
            sa.Column("text", sa.String(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("sort_index", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.CheckConstraint(
                "language IN ('en','ru')",
                name="ck_chat_starter_suggestion_language",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("language", "text", name="uq_chat_starter_suggestion_language_text"),
        )

    # Existing environments may already have the table from a partially-applied
    # or previously merged migration. Ensure the expected indexes exist without
    # failing on duplicate table/index objects.
    inspector = inspect(bind)
    existing_indexes = {idx["name"] for idx in inspector.get_indexes(_TABLE_NAME)}
    for index_name, columns in _INDEXES:
        if index_name not in existing_indexes:
            op.create_index(index_name, _TABLE_NAME, columns, unique=False)


def downgrade() -> None:
    for index_name, _columns in reversed(_INDEXES):
        op.execute(sa.text(f'DROP INDEX IF EXISTS "{index_name}"'))
    op.execute(sa.text(f'DROP TABLE IF EXISTS "{_TABLE_NAME}"'))
