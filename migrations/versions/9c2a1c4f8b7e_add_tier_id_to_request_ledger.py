"""add tier_id to request ledger

Revision ID: 9c2a1c4f8b7e
Revises: b6d9e8c1a2f3
Create Date: 2026-01-28 18:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "9c2a1c4f8b7e"
down_revision: Union[str, Sequence[str], None] = "b6d9e8c1a2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("request_ledger", sa.Column("tier_id", sa.Uuid(), nullable=True))
    op.create_index(op.f("ix_request_ledger_tier_id"), "request_ledger", ["tier_id"], unique=False)
    op.create_foreign_key(
        "fk_request_ledger_tier_id_subscription_tier",
        "request_ledger",
        "subscription_tier",
        ["tier_id"],
        ["id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("fk_request_ledger_tier_id_subscription_tier", "request_ledger", type_="foreignkey")
    op.drop_index(op.f("ix_request_ledger_tier_id"), table_name="request_ledger")
    op.drop_column("request_ledger", "tier_id")
