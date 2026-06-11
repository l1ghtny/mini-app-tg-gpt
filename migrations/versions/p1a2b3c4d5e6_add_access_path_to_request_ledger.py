"""add access path to request ledger

Revision ID: p1a2b3c4d5e6
Revises: t1a2b3c4d5e6
Create Date: 2026-06-11 17:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "p1a2b3c4d5e6"
down_revision = "t1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "request_ledger",
        sa.Column("access_path", sa.String(), nullable=True),
    )
    op.create_index(
        op.f("ix_request_ledger_access_path"),
        "request_ledger",
        ["access_path"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_request_ledger_access_path"), table_name="request_ledger")
    op.drop_column("request_ledger", "access_path")
