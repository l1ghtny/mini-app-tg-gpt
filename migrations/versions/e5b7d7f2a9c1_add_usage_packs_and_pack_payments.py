"""add usage packs and pack payments

Revision ID: e5b7d7f2a9c1
Revises: 9c2a1c4f8b7e
Create Date: 2026-01-28 20:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "e5b7d7f2a9c1"
down_revision: Union[str, Sequence[str], None] = "9c2a1c4f8b7e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'usagepacksource') THEN "
        "CREATE TYPE usagepacksource AS ENUM ('paid', 'free'); "
        "END IF; "
        "END $$;"
    )
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'usagepackstatus') THEN "
        "CREATE TYPE usagepackstatus AS ENUM ('active', 'expired'); "
        "END IF; "
        "END $$;"
    )
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'paymentproducttype') THEN "
        "CREATE TYPE paymentproducttype AS ENUM ('subscription', 'usage_pack'); "
        "END IF; "
        "END $$;"
    )

    usage_pack_source_enum = postgresql.ENUM(
        "paid",
        "free",
        name="usagepacksource",
        create_type=False,
    )
    usage_pack_status_enum = postgresql.ENUM(
        "active",
        "expired",
        name="usagepackstatus",
        create_type=False,
    )
    payment_product_enum = postgresql.ENUM(
        "subscription",
        "usage_pack",
        name="paymentproducttype",
        create_type=False,
    )

    if not inspector.has_table("usage_pack"):
        op.create_table(
            "usage_pack",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("name_ru", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("description_ru", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("price_cents", sa.Integer(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("is_public", sa.Boolean(), nullable=False),
            sa.Column("index", sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    
    usage_pack_indexes = {idx["name"] for idx in inspector.get_indexes("usage_pack")}
    if op.f("ix_usage_pack_name") not in usage_pack_indexes:
        op.create_index(op.f("ix_usage_pack_name"), "usage_pack", ["name"], unique=True)
    if op.f("ix_usage_pack_name_ru") not in usage_pack_indexes:
        op.create_index(op.f("ix_usage_pack_name_ru"), "usage_pack", ["name_ru"], unique=True)

    if not inspector.has_table("usage_pack_model_limit"):
        op.create_table(
            "usage_pack_model_limit",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("pack_id", sa.Uuid(), nullable=False),
            sa.Column("model_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("request_credits", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["pack_id"], ["usage_pack.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("pack_id", "model_name", name="uq_usage_pack_model"),
        )
    
    model_limit_indexes = {idx["name"] for idx in inspector.get_indexes("usage_pack_model_limit")}
    if op.f("ix_usage_pack_model_limit_pack_id") not in model_limit_indexes:
        op.create_index(op.f("ix_usage_pack_model_limit_pack_id"), "usage_pack_model_limit", ["pack_id"], unique=False)
    if op.f("ix_usage_pack_model_limit_model_name") not in model_limit_indexes:
        op.create_index(op.f("ix_usage_pack_model_limit_model_name"), "usage_pack_model_limit", ["model_name"], unique=False)

    if not inspector.has_table("usage_pack_image_model_limit"):
        op.create_table(
            "usage_pack_image_model_limit",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("pack_id", sa.Uuid(), nullable=False),
            sa.Column("image_model", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("credit_amount", sa.Float(), nullable=False),
            sa.ForeignKeyConstraint(["pack_id"], ["usage_pack.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("pack_id", "image_model", name="uq_usage_pack_image_model"),
        )
    
    image_limit_indexes = {idx["name"] for idx in inspector.get_indexes("usage_pack_image_model_limit")}
    if op.f("ix_usage_pack_image_model_limit_pack_id") not in image_limit_indexes:
        op.create_index(
            op.f("ix_usage_pack_image_model_limit_pack_id"),
            "usage_pack_image_model_limit",
            ["pack_id"],
            unique=False,
        )
    if op.f("ix_usage_pack_image_model_limit_image_model") not in image_limit_indexes:
        op.create_index(
            op.f("ix_usage_pack_image_model_limit_image_model"),
            "usage_pack_image_model_limit",
            ["image_model"],
            unique=False,
        )

    if not inspector.has_table("user_usage_pack"):
        op.create_table(
            "user_usage_pack",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("pack_id", sa.Uuid(), nullable=False),
            sa.Column(
                "source",
                usage_pack_source_enum,
                nullable=False,
            ),
            sa.Column(
                "status",
                usage_pack_status_enum,
                nullable=False,
            ),
            sa.Column("purchased_at", sa.DateTime(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("payment_id", sa.Uuid(), nullable=True),
            sa.Column("note", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.ForeignKeyConstraint(["pack_id"], ["usage_pack.id"]),
            sa.ForeignKeyConstraint(["payment_id"], ["payment.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["app_user.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    
    user_usage_pack_indexes = {idx["name"] for idx in inspector.get_indexes("user_usage_pack")}
    if op.f("ix_user_usage_pack_user_id") not in user_usage_pack_indexes:
        op.create_index(op.f("ix_user_usage_pack_user_id"), "user_usage_pack", ["user_id"], unique=False)
    if op.f("ix_user_usage_pack_pack_id") not in user_usage_pack_indexes:
        op.create_index(op.f("ix_user_usage_pack_pack_id"), "user_usage_pack", ["pack_id"], unique=False)
    if op.f("ix_user_usage_pack_purchased_at") not in user_usage_pack_indexes:
        op.create_index(op.f("ix_user_usage_pack_purchased_at"), "user_usage_pack", ["purchased_at"], unique=False)
    if op.f("ix_user_usage_pack_expires_at") not in user_usage_pack_indexes:
        op.create_index(op.f("ix_user_usage_pack_expires_at"), "user_usage_pack", ["expires_at"], unique=False)

    ledger_columns = {col["name"] for col in inspector.get_columns("request_ledger")}
    if "usage_pack_id" not in ledger_columns:
        op.add_column("request_ledger", sa.Column("usage_pack_id", sa.Uuid(), nullable=True))
    
    ledger_indexes = {idx["name"] for idx in inspector.get_indexes("request_ledger")}
    if op.f("ix_request_ledger_usage_pack_id") not in ledger_indexes:
        op.create_index(op.f("ix_request_ledger_usage_pack_id"), "request_ledger", ["usage_pack_id"], unique=False)
    
    ledger_fks = inspector.get_foreign_keys("request_ledger")
    ledger_fk_name = "fk_request_ledger_usage_pack_id_user_usage_pack"
    if not any(fk["name"] == ledger_fk_name for fk in ledger_fks):
        op.create_foreign_key(
            ledger_fk_name,
            "request_ledger",
            "user_usage_pack",
            ["usage_pack_id"],
            ["id"],
        )

    payment_columns = {col["name"] for col in inspector.get_columns("payment")}
    if "product_type" not in payment_columns:
        op.add_column(
            "payment",
            sa.Column(
                "product_type",
                payment_product_enum,
                nullable=False,
                server_default=sa.text("'subscription'"),
            ),
        )
    if "pack_id" not in payment_columns:
        op.add_column("payment", sa.Column("pack_id", sa.Uuid(), nullable=True))
    
    payment_indexes = {idx["name"] for idx in inspector.get_indexes("payment")}
    if op.f("ix_payment_product_type") not in payment_indexes:
        op.create_index(op.f("ix_payment_product_type"), "payment", ["product_type"], unique=False)
    if op.f("ix_payment_pack_id") not in payment_indexes:
        op.create_index(op.f("ix_payment_pack_id"), "payment", ["pack_id"], unique=False)
    
    payment_fks = inspector.get_foreign_keys("payment")
    payment_fk_name = "fk_payment_pack_id_usage_pack"
    if not any(fk["name"] == payment_fk_name for fk in payment_fks):
        op.create_foreign_key(
            payment_fk_name,
            "payment",
            "usage_pack",
            ["pack_id"],
            ["id"],
        )

    access_code_fks = inspector.get_foreign_keys("access_code")
    access_code_columns = {col["name"] for col in inspector.get_columns("access_code")}
    if "usage_pack_id" not in access_code_columns:
        op.add_column("access_code", sa.Column("usage_pack_id", sa.Uuid(), nullable=True))
    
    access_code_fk_name = "fk_access_code_usage_pack_id_usage_pack"
    if not any(fk["name"] == access_code_fk_name for fk in access_code_fks):
        op.create_foreign_key(
            access_code_fk_name,
            "access_code",
            "usage_pack",
            ["usage_pack_id"],
            ["id"],
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("fk_access_code_usage_pack_id_usage_pack", "access_code", type_="foreignkey")
    op.drop_column("access_code", "usage_pack_id")

    op.drop_constraint("fk_payment_pack_id_usage_pack", "payment", type_="foreignkey")
    op.drop_index(op.f("ix_payment_pack_id"), table_name="payment")
    op.drop_index(op.f("ix_payment_product_type"), table_name="payment")
    op.drop_column("payment", "pack_id")
    op.drop_column("payment", "product_type")
    op.execute("DROP TYPE IF EXISTS paymentproducttype")

    op.drop_constraint("fk_request_ledger_usage_pack_id_user_usage_pack", "request_ledger", type_="foreignkey")
    op.drop_index(op.f("ix_request_ledger_usage_pack_id"), table_name="request_ledger")
    op.drop_column("request_ledger", "usage_pack_id")

    op.drop_index(op.f("ix_user_usage_pack_expires_at"), table_name="user_usage_pack")
    op.drop_index(op.f("ix_user_usage_pack_purchased_at"), table_name="user_usage_pack")
    op.drop_index(op.f("ix_user_usage_pack_pack_id"), table_name="user_usage_pack")
    op.drop_index(op.f("ix_user_usage_pack_user_id"), table_name="user_usage_pack")
    op.drop_table("user_usage_pack")
    op.execute("DROP TYPE IF EXISTS usagepackstatus")
    op.execute("DROP TYPE IF EXISTS usagepacksource")

    op.drop_index(op.f("ix_usage_pack_image_model_limit_image_model"), table_name="usage_pack_image_model_limit")
    op.drop_index(op.f("ix_usage_pack_image_model_limit_pack_id"), table_name="usage_pack_image_model_limit")
    op.drop_table("usage_pack_image_model_limit")

    op.drop_index(op.f("ix_usage_pack_model_limit_model_name"), table_name="usage_pack_model_limit")
    op.drop_index(op.f("ix_usage_pack_model_limit_pack_id"), table_name="usage_pack_model_limit")
    op.drop_table("usage_pack_model_limit")

    op.drop_index(op.f("ix_usage_pack_name_ru"), table_name="usage_pack")
    op.drop_index(op.f("ix_usage_pack_name"), table_name="usage_pack")
    op.drop_table("usage_pack")
