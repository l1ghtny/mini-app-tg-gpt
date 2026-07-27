"""add provider-neutral identities and web auth challenges

Revision ID: o1a2b3c4d5e6
Revises: n1a2b3c4d5e6
Create Date: 2026-07-26 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "o1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "n1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("app_user", "telegram_id", existing_type=sa.BigInteger(), nullable=True)

    op.create_table(
        "user_identity",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("provider IN ('telegram','email')", name="ck_user_identity_provider"),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "subject", name="uq_user_identity_provider_subject"),
    )
    op.create_index("ix_user_identity_user_id", "user_identity", ["user_id"])
    op.create_index("ix_user_identity_provider", "user_identity", ["provider"])
    op.create_index("ix_user_identity_subject", "user_identity", ["subject"])
    op.create_index("ix_user_identity_email", "user_identity", ["email"])
    op.create_index("ix_user_identity_created_at", "user_identity", ["created_at"])
    op.create_index("ix_user_identity_last_used_at", "user_identity", ["last_used_at"])

    op.create_table(
        "web_auth_challenge",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("target_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["target_user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_web_auth_challenge_token_hash", "web_auth_challenge", ["token_hash"], unique=True)
    op.create_index("ix_web_auth_challenge_email", "web_auth_challenge", ["email"])
    op.create_index("ix_web_auth_challenge_target_user_id", "web_auth_challenge", ["target_user_id"])
    op.create_index("ix_web_auth_challenge_created_at", "web_auth_challenge", ["created_at"])
    op.create_index("ix_web_auth_challenge_expires_at", "web_auth_challenge", ["expires_at"])
    op.create_index("ix_web_auth_challenge_consumed_at", "web_auth_challenge", ["consumed_at"])

    op.execute(
        sa.text(
            """
            INSERT INTO user_identity (
                id, user_id, provider, subject, email, created_at, last_used_at
            )
            SELECT
                md5(id::text || '-telegram')::uuid,
                id,
                'telegram',
                telegram_id::text,
                NULL,
                now(),
                now()
            FROM app_user
            WHERE telegram_id IS NOT NULL
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM app_user WHERE telegram_id IS NULL) THEN
                    RAISE EXCEPTION 'Cannot downgrade web auth while web-only users exist';
                END IF;
            END
            $$;
            """
        )
    )

    op.drop_index("ix_web_auth_challenge_consumed_at", table_name="web_auth_challenge")
    op.drop_index("ix_web_auth_challenge_expires_at", table_name="web_auth_challenge")
    op.drop_index("ix_web_auth_challenge_created_at", table_name="web_auth_challenge")
    op.drop_index("ix_web_auth_challenge_target_user_id", table_name="web_auth_challenge")
    op.drop_index("ix_web_auth_challenge_email", table_name="web_auth_challenge")
    op.drop_index("ix_web_auth_challenge_token_hash", table_name="web_auth_challenge")
    op.drop_table("web_auth_challenge")

    op.drop_index("ix_user_identity_last_used_at", table_name="user_identity")
    op.drop_index("ix_user_identity_created_at", table_name="user_identity")
    op.drop_index("ix_user_identity_email", table_name="user_identity")
    op.drop_index("ix_user_identity_subject", table_name="user_identity")
    op.drop_index("ix_user_identity_provider", table_name="user_identity")
    op.drop_index("ix_user_identity_user_id", table_name="user_identity")
    op.drop_table("user_identity")
    op.alter_column("app_user", "telegram_id", existing_type=sa.BigInteger(), nullable=False)
