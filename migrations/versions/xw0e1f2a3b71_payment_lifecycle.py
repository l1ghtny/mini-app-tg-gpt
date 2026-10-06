"""Separate payment event application from mutable provider status."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "xw0e1f2a3b71"
down_revision = "xw0e1f2a3b70"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "subscription_tier", sa.Column("allowance_plan_key", sa.String(), nullable=True)
    )
    op.execute(
        "UPDATE subscription_tier SET allowance_plan_key = 'start' WHERE name = 'Basic'"
    )
    op.add_column(
        "payment",
        sa.Column(
            "confirmation_applied",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "payment",
        sa.Column(
            "refund_applied", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "payment",
        sa.Column(
            "subscription_id",
            UUID(),
            sa.ForeignKey("user_subscription.id"),
            nullable=True,
        ),
    )
    op.create_index("ix_payment_subscription_id", "payment", ["subscription_id"])
    for column in (
        "confirmed_at",
        "subscription_period_start",
        "subscription_period_end",
        "refund_requested_at",
        "refunded_at",
    ):
        op.add_column("payment", sa.Column(column, sa.DateTime(), nullable=True))
    op.execute(
        "UPDATE payment SET confirmation_applied = true WHERE tbank_status IN ('CONFIRMED','REFUNDING','REFUNDED','PARTIAL_REFUNDED')"
    )
    op.execute(
        "UPDATE payment SET refund_applied = true WHERE tbank_status = 'REFUNDED'"
    )


def downgrade():
    op.drop_index("ix_payment_subscription_id", table_name="payment")
    for column in (
        "refunded_at",
        "refund_requested_at",
        "subscription_period_end",
        "subscription_period_start",
        "confirmed_at",
        "subscription_id",
        "refund_applied",
        "confirmation_applied",
    ):
        op.drop_column("payment", column)
    op.drop_column("subscription_tier", "allowance_plan_key")
