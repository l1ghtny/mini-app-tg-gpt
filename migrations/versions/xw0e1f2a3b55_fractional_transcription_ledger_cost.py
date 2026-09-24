"""Store fractional transcription minutes in request ledger cost.

Revision ID: xw0e1f2a3b55
Revises: xw0e1f2a3b54
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "xw0e1f2a3b55"
down_revision = "xw0e1f2a3b54"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This takes an ACCESS EXCLUSIVE lock and rewrites the table. Abort fast
    # if traffic prevents prompt lock acquisition or the rewrite is unusually
    # slow; the migration transaction leaves the original column intact.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    op.alter_column(
        "request_ledger", "cost",
        existing_type=sa.Integer(),
        type_=postgresql.DOUBLE_PRECISION(),
        existing_nullable=True,
        postgresql_using="cost::double precision",
    )


def downgrade() -> None:
    bind = op.get_bind()
    has_unrepresentable = bind.execute(sa.text("""
        SELECT EXISTS (
            SELECT 1 FROM request_ledger
            WHERE cost IS NOT NULL AND (
                cost <> trunc(cost)
                OR cost > 2147483647
                OR cost < -2147483648
            )
        )
    """)).scalar_one()
    if has_unrepresentable:
        raise RuntimeError("Cannot downgrade request_ledger.cost with fractional or out-of-range values")
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    op.alter_column(
        "request_ledger", "cost",
        existing_type=postgresql.DOUBLE_PRECISION(),
        type_=sa.Integer(),
        existing_nullable=True,
        postgresql_using="cost::integer",
    )
