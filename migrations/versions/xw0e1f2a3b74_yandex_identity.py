"""Allow Yandex identities in the existing canonical account model."""

from alembic import op

revision = "xw0e1f2a3b74"
down_revision = "xw0e1f2a3b73"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_user_identity_provider", "user_identity", type_="check")
    op.create_check_constraint(
        "ck_user_identity_provider",
        "user_identity",
        "provider IN ('telegram','email','yandex')",
    )


def downgrade():
    # Refuse rollback while real Yandex identities exist; never erase login data.
    op.create_check_constraint(
        "ck_user_identity_provider_old",
        "user_identity",
        "provider IN ('telegram','email')",
    )
    op.drop_constraint("ck_user_identity_provider", "user_identity", type_="check")
    op.execute(
        "ALTER TABLE user_identity RENAME CONSTRAINT ck_user_identity_provider_old TO ck_user_identity_provider"
    )
