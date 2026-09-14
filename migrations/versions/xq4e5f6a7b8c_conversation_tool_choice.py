"""Persist conversation tool selection without changing provider semantics."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "xq4e5f6a7b8c"
down_revision = "xp3d4e5f6a7b"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("conversation", sa.Column(
        "tool_choice", postgresql.JSONB(), nullable=False,
        server_default=sa.text("'\"auto\"'::jsonb"),
    ))


def downgrade():
    op.drop_column("conversation", "tool_choice")
