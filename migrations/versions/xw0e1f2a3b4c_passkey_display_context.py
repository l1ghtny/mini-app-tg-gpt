"""Add optional passkey browser context; leave historical devices unknown."""

from alembic import op
import sqlalchemy as sa

revision = "xw0e1f2a3b4c"
down_revision = "xv9d0e1f2a3b"
branch_labels = None
depends_on = None

_COLUMNS = ("created_browser", "created_os", "last_used_browser", "last_used_os")


def upgrade():
    existing = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("passkey_credential")
    }
    for name in _COLUMNS:
        if name not in existing:
            op.add_column(
                "passkey_credential", sa.Column(name, sa.String(), nullable=True)
            )


def downgrade():
    for name in reversed(_COLUMNS):
        op.drop_column("passkey_credential", name)
