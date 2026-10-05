"""Preserve uploaded file names without changing provider filenames."""
from alembic import op
import sqlalchemy as sa

revision = "xw0e1f2a3b65"
down_revision = "xw0e1f2a3b64"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user_document", sa.Column("original_filename", sa.String(), nullable=True))
    op.add_column("user_document", sa.Column("retention_migrated_at", sa.DateTime(), nullable=True))
    op.create_index("ix_user_document_retention_migrated_at", "user_document", ["retention_migrated_at"])


def downgrade():
    op.drop_index("ix_user_document_retention_migrated_at", table_name="user_document")
    op.drop_column("user_document", "retention_migrated_at")
    op.drop_column("user_document", "original_filename")
