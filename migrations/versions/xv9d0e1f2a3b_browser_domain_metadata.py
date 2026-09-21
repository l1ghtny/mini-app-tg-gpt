"""Add browser-origin and passkey-RP metadata without guessing legacy values."""
from alembic import op
import sqlalchemy as sa

revision = "xv9d0e1f2a3b"
down_revision = "xu8c9d0e1f2a"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("web_auth_challenge", sa.Column("browser_origin", sa.String(), nullable=True))
    op.add_column("passkey_credential", sa.Column("rp_id", sa.String(), nullable=True))


def downgrade():
    op.drop_column("passkey_credential", "rp_id")
    op.drop_column("web_auth_challenge", "browser_origin")
