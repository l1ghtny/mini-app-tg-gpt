"""Add a tier-level Work allowance override.

Revision ID: xi6d7e8f9a0b
Revises: xh5c6d7e8f9
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "xi6d7e8f9a0b"
down_revision: Union[str, Sequence[str], None] = "xh5c6d7e8f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "subscription_tier",
        sa.Column("monthly_work_runs", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_subscription_tier_monthly_work_runs_positive",
        "subscription_tier",
        "monthly_work_runs IS NULL OR monthly_work_runs > 0",
    )
    op.execute(
        sa.text(
            """
            UPDATE subscription_tier
            SET monthly_work_runs = 250
            WHERE lower(name) = 'smooth tier'
            """
        )
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_subscription_tier_monthly_work_runs_positive",
        "subscription_tier",
        type_="check",
    )
    op.drop_column("subscription_tier", "monthly_work_runs")
