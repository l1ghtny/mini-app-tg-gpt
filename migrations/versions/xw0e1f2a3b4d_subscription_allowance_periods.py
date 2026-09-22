"""Track subscription-aligned allowance cycles without resetting balances."""

from alembic import op

revision = "xw0e1f2a3b4d"
down_revision = "xw0e1f2a3b4c"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE allowance_account ADD COLUMN IF NOT EXISTS subscription_anchor TIMESTAMP WITHOUT TIME ZONE"
    )


def downgrade():
    # Retain financial period provenance on application rollback.
    pass
