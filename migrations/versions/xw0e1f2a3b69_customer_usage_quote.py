"""Keep the customer quote and its allowance basis beside immutable accounting."""
from alembic import op
import sqlalchemy as sa

revision = "xw0e1f2a3b69"
down_revision = "xw0e1f2a3b68"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("allowance_request", sa.Column("customer_quote", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("allowance_request", "customer_quote")
