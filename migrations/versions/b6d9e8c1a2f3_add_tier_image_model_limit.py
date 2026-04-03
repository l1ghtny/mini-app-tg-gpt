"""add tier image model limit

Revision ID: b6d9e8c1a2f3
Revises: 1558e84cc1e1
Create Date: 2026-01-28 14:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "b6d9e8c1a2f3"
down_revision: Union[str, Sequence[str], None] = "1558e84cc1e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("tier_image_model_limit"):
        op.create_table(
            "tier_image_model_limit",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("tier_id", sa.Uuid(), nullable=False),
            sa.Column("image_model", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("monthly_requests", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["tier_id"], ["subscription_tier.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tier_id", "image_model", name="uq_tier_image_model"),
        )

    indexes = {idx["name"] for idx in inspector.get_indexes("tier_image_model_limit")}
    
    img_model_index = op.f("ix_tier_image_model_limit_image_model")
    if img_model_index not in indexes:
        op.create_index(
            img_model_index,
            "tier_image_model_limit",
            ["image_model"],
            unique=False,
        )
    
    tier_id_index = op.f("ix_tier_image_model_limit_tier_id")
    if tier_id_index not in indexes:
        op.create_index(
            tier_id_index,
            "tier_image_model_limit",
            ["tier_id"],
            unique=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_tier_image_model_limit_tier_id"), table_name="tier_image_model_limit")
    op.drop_index(op.f("ix_tier_image_model_limit_image_model"), table_name="tier_image_model_limit")
    op.drop_table("tier_image_model_limit")
