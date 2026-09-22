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
    op.execute("""CREATE TABLE IF NOT EXISTS allowance_control (
        id VARCHAR PRIMARY KEY,
        period_mode VARCHAR NOT NULL,
        updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
        CONSTRAINT ck_allowance_period_mode CHECK (period_mode IN ('calendar', 'paused', 'subscription'))
    )""")
    op.execute("""INSERT INTO allowance_control (id, period_mode, updated_at)
        VALUES ('subscription_periods', 'calendar', timezone('utc', now()))
        ON CONFLICT (id) DO NOTHING""")


def downgrade():
    # Retain financial period provenance on application rollback.
    pass
