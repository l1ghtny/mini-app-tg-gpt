"""Persist generation plans and bounded recovery funding.

Revision ID: xw0e1f2a3b62
Revises: xw0e1f2a3b61
"""

from alembic import op
import sqlalchemy as sa

revision = "xw0e1f2a3b62"
down_revision = "xw0e1f2a3b61"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "allowance_account",
        sa.Column(
            "grant_policy_version",
            sa.String(),
            nullable=False,
            server_default="2026-09-18-v1",
        ),
    )
    op.add_column(
        "allowance_request", sa.Column("execution_plan", sa.JSON(), nullable=True)
    )
    op.add_column(
        "allowance_request",
        sa.Column(
            "recovery_ceiling", sa.BigInteger(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "allowance_provider_attempt",
        sa.Column("recovery", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_check_constraint(
        "ck_allowance_recovery_ceiling", "allowance_request", "recovery_ceiling >= 0"
    )


def downgrade():
    op.drop_constraint(
        "ck_allowance_recovery_ceiling", "allowance_request", type_="check"
    )
    op.drop_column("allowance_provider_attempt", "recovery")
    op.drop_column("allowance_request", "recovery_ceiling")
    op.drop_column("allowance_request", "execution_plan")
    op.drop_column("allowance_account", "grant_policy_version")
