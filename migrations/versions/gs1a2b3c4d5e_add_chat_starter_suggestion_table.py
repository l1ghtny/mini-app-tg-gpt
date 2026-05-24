"""add chat_starter_suggestion table

Revision ID: gs1a2b3c4d5e
Revises: gd2a3b4c5d6e
Create Date: 2026-05-24 12:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "gs1a2b3c4d5e"
down_revision: Union[str, None] = "gd2a3b4c5d6e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_starter_suggestion",
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
    op.create_index(
        "ix_chat_starter_suggestion_language",
        "chat_starter_suggestion",
        ["language"],
        unique=False,
    )
    op.create_index(
        "ix_chat_starter_suggestion_is_active",
        "chat_starter_suggestion",
        ["is_active"],
        unique=False,
    )
    op.create_index(
        "ix_chat_starter_suggestion_created_at",
        "chat_starter_suggestion",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_chat_starter_suggestion_active_lang",
        "chat_starter_suggestion",
        ["is_active", "language"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_chat_starter_suggestion_active_lang", table_name="chat_starter_suggestion")
    op.drop_index("ix_chat_starter_suggestion_created_at", table_name="chat_starter_suggestion")
    op.drop_index("ix_chat_starter_suggestion_is_active", table_name="chat_starter_suggestion")
    op.drop_index("ix_chat_starter_suggestion_language", table_name="chat_starter_suggestion")
    op.drop_table("chat_starter_suggestion")
