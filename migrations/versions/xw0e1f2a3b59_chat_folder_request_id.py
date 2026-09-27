"""Make project creation idempotent for a client request."""

from alembic import context, op
import sqlalchemy as sa

revision = "xw0e1f2a3b59"
down_revision = "xw0e1f2a3b58"
branch_labels = None
depends_on = None

CONSTRAINT = "uq_chat_folder_user_request"


def upgrade():
    if context.is_offline_mode():
        op.add_column(
            "chat_folder", sa.Column("client_request_id", sa.Uuid(), nullable=True)
        )
        op.create_unique_constraint(
            CONSTRAINT, "chat_folder", ["user_id", "client_request_id"]
        )
        return

    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("chat_folder")}
    if "client_request_id" not in columns:
        op.add_column(
            "chat_folder", sa.Column("client_request_id", sa.Uuid(), nullable=True)
        )

    inspector = sa.inspect(op.get_bind())
    constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("chat_folder")
    }
    if CONSTRAINT not in constraints:
        op.create_unique_constraint(
            CONSTRAINT, "chat_folder", ["user_id", "client_request_id"]
        )


def downgrade():
    if context.is_offline_mode():
        op.drop_constraint(CONSTRAINT, "chat_folder", type_="unique")
        op.drop_column("chat_folder", "client_request_id")
        return

    inspector = sa.inspect(op.get_bind())
    constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("chat_folder")
    }
    if CONSTRAINT in constraints:
        op.drop_constraint(CONSTRAINT, "chat_folder", type_="unique")

    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("chat_folder")}
    if "client_request_id" in columns:
        op.drop_column("chat_folder", "client_request_id")
