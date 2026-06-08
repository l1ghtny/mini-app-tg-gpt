"""add general_discount table

Revision ID: gd1a2b3c4d5e
Revises: gc1a2b3c4d5e
Create Date: 2026-05-23 16:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "gd1a2b3c4d5e"
down_revision: Union[str, None] = "gc1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "general_discount",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(), nullable=True),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("percent_off", sa.Integer(), nullable=False),
        sa.Column("applies_to_tiers", JSONB(), nullable=True),
        sa.Column("conditions", JSONB(), nullable=True),
        sa.Column("starts_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("stackable", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_general_discount_code"),
    )
    op.create_index("ix_general_discount_code", "general_discount", ["code"], unique=True)
    op.create_index("ix_general_discount_type", "general_discount", ["type"])
    op.create_index("ix_general_discount_is_active", "general_discount", ["is_active"])
    op.create_index("ix_general_discount_starts_at", "general_discount", ["starts_at"])
    op.create_index("ix_general_discount_expires_at", "general_discount", ["expires_at"])
    op.create_index(
        "ix_general_discount_active_type", "general_discount", ["is_active", "type"]
    )


def downgrade() -> None:
    op.drop_index("ix_general_discount_active_type", table_name="general_discount")
    op.drop_index("ix_general_discount_expires_at", table_name="general_discount")
    op.drop_index("ix_general_discount_starts_at", table_name="general_discount")
    op.drop_index("ix_general_discount_is_active", table_name="general_discount")
    op.drop_index("ix_general_discount_type", table_name="general_discount")
    op.drop_index("ix_general_discount_code", table_name="general_discount")
    op.drop_table("general_discount")
