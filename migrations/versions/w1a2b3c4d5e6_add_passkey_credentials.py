"""add passkey credentials

Revision ID: w1a2b3c4d5e6
Revises: v1a2b3c4d5e6
Create Date: 2026-07-28 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "w1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "v1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "passkey_credential",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("credential_id", sa.String(), nullable=False),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.Integer(), nullable=False),
        sa.Column(
            "transports", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("device_type", sa.String(), nullable=True),
        sa.Column("backed_up", sa.Boolean(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_passkey_credential_user_id", "passkey_credential", ["user_id"])
    op.create_index(
        "ix_passkey_credential_credential_id",
        "passkey_credential",
        ["credential_id"],
        unique=True,
    )
    op.create_index(
        "ix_passkey_credential_created_at", "passkey_credential", ["created_at"]
    )
    op.create_index(
        "ix_passkey_credential_last_used_at", "passkey_credential", ["last_used_at"]
    )


def downgrade() -> None:
    op.drop_table("passkey_credential")
