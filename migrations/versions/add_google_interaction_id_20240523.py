"""Add last_google_interaction_id column to conversation table"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "ab20240523"
down_revision = "ed3f5ae64826"  # last migration before this
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column(
        "conversation",
        sa.Column("last_google_interaction_id", sa.String(), nullable=True, index=True),
    )
    # Index is created automatically by column definition

def downgrade() -> None:
    op.drop_index("ix_conversation_last_google_interaction_id", table_name="conversation")
    op.drop_column("conversation", "last_google_interaction_id")
