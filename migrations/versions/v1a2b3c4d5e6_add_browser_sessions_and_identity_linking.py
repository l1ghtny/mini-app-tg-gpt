"""add revocable browser sessions and Telegram identity linking

Revision ID: v1a2b3c4d5e6
Revises: q1a2b3c4d5e6
Create Date: 2026-07-27 20:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "v1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "q1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("app_user", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.create_index("ix_app_user_deleted_at", "app_user", ["deleted_at"])

    op.create_table(
        "browser_session",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("user_id", "created_at", "last_seen_at", "expires_at", "revoked_at"):
        op.create_index(f"ix_browser_session_{column}", "browser_session", [column])
    op.create_index("ix_browser_session_token_hash", "browser_session", ["token_hash"], unique=True)

    op.create_table(
        "telegram_link_challenge",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("target_user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("conflicting_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','linked','conflict','expired')",
            name="ck_telegram_link_challenge_status",
        ),
        sa.ForeignKeyConstraint(["target_user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conflicting_user_id"], ["app_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "target_user_id", "status", "telegram_id", "conflicting_user_id",
        "created_at", "expires_at", "consumed_at",
    ):
        op.create_index(f"ix_telegram_link_challenge_{column}", "telegram_link_challenge", [column])
    op.create_index(
        "ix_telegram_link_challenge_token_hash",
        "telegram_link_challenge",
        ["token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("telegram_link_challenge")
    op.drop_table("browser_session")
    op.drop_index("ix_app_user_deleted_at", table_name="app_user")
    op.drop_column("app_user", "deleted_at")
