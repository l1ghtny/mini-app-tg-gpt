"""Private generated DOCX/PDF chat results and immutable revision sources."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "xw0e1f2a3b72"
down_revision = "xw0e1f2a3b71"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("chat_document",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("user_id", UUID(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("conversation_id", UUID(), nullable=False),
        sa.Column("request_key", sa.String(), nullable=False, unique=True),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("format", sa.String(), nullable=False),
        sa.Column("spec", JSONB(), nullable=False),
        sa.Column("parent_id", UUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("bucket", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("cleanup_attempted_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    for column in ("user_id", "conversation_id", "expires_at"):
        op.create_index(f"ix_chat_document_{column}", "chat_document", [column])


def downgrade():
    op.drop_table("chat_document")
