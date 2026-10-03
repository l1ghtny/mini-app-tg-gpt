"""Add non-consuming customer caps and leased logical task ownership."""

from alembic import op
import sqlalchemy as sa

revision = "xw0e1f2a3b63"
down_revision = "xw0e1f2a3b62"
branch_labels = depends_on = None


def upgrade():
    for column in (
        sa.Column(
            "admission_policy",
            sa.String(),
            nullable=False,
            server_default="held-legacy",
        ),
        sa.Column(
            "supplier_ceiling", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("risk_policy", sa.JSON(), nullable=True),
        sa.Column("execution_state", sa.JSON(), nullable=True),
        sa.Column("task_owner", sa.String(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("task_deadline_at", sa.DateTime(), nullable=True),
        sa.Column("admission_period_start", sa.DateTime(), nullable=True),
    ):
        op.add_column("allowance_request", column)
    op.create_index(
        "ix_allowance_active_tasks",
        "allowance_request",
        ["user_id", "status", "lease_expires_at"],
    )


def downgrade():
    op.drop_index("ix_allowance_active_tasks", table_name="allowance_request")
    for name in (
        "admission_period_start",
        "task_deadline_at",
        "lease_expires_at",
        "task_owner",
        "execution_state",
        "risk_policy",
        "supplier_ceiling",
        "admission_policy",
    ):
        op.drop_column("allowance_request", name)
