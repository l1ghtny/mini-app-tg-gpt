"""add tier image quality limits

Revision ID: f3a1b2c4d5e7
Revises: e5b7d7f2a9c1
Create Date: 2026-01-29 16:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "f3a1b2c4d5e7"
down_revision: Union[str, Sequence[str], None] = "e5b7d7f2a9c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "tier_image_quality_limit",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tier_id", sa.Uuid(), nullable=False),
        sa.Column("quality", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(["tier_id"], ["subscription_tier.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tier_id", "quality", name="uq_tier_image_quality"),
    )
    op.create_index(
        op.f("ix_tier_image_quality_limit_tier_id"),
        "tier_image_quality_limit",
        ["tier_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_tier_image_quality_limit_quality"),
        "tier_image_quality_limit",
        ["quality"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_tier_image_quality_limit_quality"), table_name="tier_image_quality_limit")
    op.drop_index(op.f("ix_tier_image_quality_limit_tier_id"), table_name="tier_image_quality_limit")
    op.drop_table("tier_image_quality_limit")
