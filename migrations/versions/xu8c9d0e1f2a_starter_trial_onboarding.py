"""One-time starter clock and account-scoped onboarding visits; no user cutover."""
from alembic import op

revision = "xu8c9d0e1f2a"
down_revision = "xt7b8c9d0e1f"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE allowance_account ADD COLUMN IF NOT EXISTS trial_started_at TIMESTAMP WITHOUT TIME ZONE")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_allowance_trial_user ON allowance_account (user_id) WHERE plan = 'starter'")
    op.execute("""CREATE TABLE IF NOT EXISTS onboarding_visit (
        user_id UUID NOT NULL REFERENCES app_user(id), session_id UUID NOT NULL,
        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
        PRIMARY KEY (user_id, session_id))""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_onboarding_visit_created_at ON onboarding_visit(created_at)")


def downgrade():
    # Financial activation history must survive rollback.
    pass
