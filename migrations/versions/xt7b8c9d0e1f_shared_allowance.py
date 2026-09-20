"""Add shared allowance accounting; no public tier or user mutations."""

from alembic import op

revision = "xt7b8c9d0e1f"
down_revision = "xs6a7b8c9d0e"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE IF NOT EXISTS allowance_account (
    id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    scope VARCHAR NOT NULL, 
    period_start TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    period_end TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    plan VARCHAR NOT NULL, 
    rate_version VARCHAR NOT NULL, 
    granted BIGINT NOT NULL, 
    spent BIGINT NOT NULL, 
    reserved BIGINT NOT NULL, 
    luna_granted BIGINT NOT NULL, 
    luna_spent BIGINT NOT NULL, 
    luna_reserved BIGINT NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_allowance_period UNIQUE (user_id, scope, period_start), 
    CONSTRAINT ck_allowance_balance CHECK (granted >= 0 AND spent >= 0 AND reserved >= 0 AND spent + reserved <= granted), 
    CONSTRAINT ck_allowance_luna CHECK (luna_granted >= 0 AND luna_spent >= 0 AND luna_reserved >= 0 AND luna_spent + luna_reserved <= luna_granted), 
    FOREIGN KEY(user_id) REFERENCES app_user (id)
)
""")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_allowance_account_scope ON allowance_account (scope)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_allowance_account_user_id ON allowance_account (user_id)"
    )
    op.execute("""
CREATE TABLE IF NOT EXISTS allowance_request (
    id UUID NOT NULL, 
    account_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    scope VARCHAR NOT NULL, 
    request_id VARCHAR NOT NULL, 
    conversation_id UUID, 
    model VARCHAR NOT NULL, 
    ceiling BIGINT NOT NULL, 
    charged BIGINT NOT NULL, 
    luna_ceiling BIGINT NOT NULL, 
    luna_charged BIGINT NOT NULL, 
    status VARCHAR NOT NULL, 
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_allowance_request UNIQUE (user_id, scope, request_id), 
    FOREIGN KEY(account_id) REFERENCES allowance_account (id), 
    FOREIGN KEY(user_id) REFERENCES app_user (id)
)
""")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_allowance_request_account_id ON allowance_request (account_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_allowance_request_user_id ON allowance_request (user_id)"
    )
    op.execute("""
CREATE TABLE IF NOT EXISTS allowance_event (
    id UUID NOT NULL, 
    account_id UUID NOT NULL, 
    request_id UUID, 
    event_key VARCHAR NOT NULL, 
    kind VARCHAR NOT NULL, 
    units BIGINT NOT NULL, 
    luna_units BIGINT NOT NULL, 
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(account_id) REFERENCES allowance_account (id), 
    FOREIGN KEY(request_id) REFERENCES allowance_request (id), 
    UNIQUE (event_key)
)
""")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_allowance_event_account_id ON allowance_event (account_id)"
    )
    op.execute("""
CREATE TABLE IF NOT EXISTS allowance_provider_attempt (
    id UUID NOT NULL, 
    request_id UUID NOT NULL, 
    step_key VARCHAR NOT NULL, 
    model VARCHAR NOT NULL, 
    provider_id VARCHAR, 
    status VARCHAR NOT NULL, 
    budget BIGINT NOT NULL, 
    supplier_units BIGINT, 
    customer_units BIGINT NOT NULL, 
    included BOOLEAN NOT NULL, 
    input_tokens INTEGER NOT NULL, 
    cached_tokens INTEGER NOT NULL, 
    cache_write_tokens INTEGER NOT NULL, 
    output_tokens INTEGER NOT NULL, 
    reasoning_tokens INTEGER NOT NULL, 
    search_calls INTEGER NOT NULL, 
    file_calls INTEGER NOT NULL, 
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_allowance_attempt UNIQUE (request_id, step_key), 
    FOREIGN KEY(request_id) REFERENCES allowance_request (id)
)
""")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_allowance_provider_attempt_request_id ON allowance_provider_attempt (request_id)"
    )

    op.execute(
        "ALTER TABLE allowance_provider_attempt ADD COLUMN IF NOT EXISTS usage_details JSON"
    )
    op.execute(
        "ALTER TABLE allowance_provider_attempt ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP WITHOUT TIME ZONE"
    )


def downgrade():
    op.drop_table("allowance_provider_attempt")
    op.drop_table("allowance_event")
    op.drop_table("allowance_request")
    op.drop_table("allowance_account")
