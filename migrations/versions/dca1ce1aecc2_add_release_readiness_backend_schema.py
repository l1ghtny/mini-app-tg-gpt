"""add release readiness backend schema

Revision ID: dca1ce1aecc2
Revises: t1a2b3c4d5e6
Create Date: 2026-06-04 11:10:04.630911

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'dca1ce1aecc2'
down_revision: Union[str, Sequence[str], None] = 't1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return inspector.has_table(table_name)


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    if not _table_exists(inspector, table_name):
        return False
    return any(col.get("name") == column_name for col in inspector.get_columns(table_name))


def _index_exists(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    if not _table_exists(inspector, table_name):
        return False
    return any(idx.get("name") == index_name for idx in inspector.get_indexes(table_name))


def _unique_exists(inspector: sa.Inspector, table_name: str, unique_name: str) -> bool:
    if not _table_exists(inspector, table_name):
        return False
    return any(uc.get("name") == unique_name for uc in inspector.get_unique_constraints(table_name))


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _column_exists(inspector, "app_user", "default_document_provider") is False:
        op.add_column(
            "app_user",
            sa.Column("default_document_provider", sa.String(), nullable=False, server_default="openai"),
        )
        op.execute(sa.text("UPDATE app_user SET default_document_provider = 'openai' WHERE default_document_provider IS NULL"))
        op.alter_column("app_user", "default_document_provider", server_default=None)

    if _column_exists(inspector, "payment_methods", "status") is False:
        op.add_column("payment_methods", sa.Column("status", sa.String(), nullable=False, server_default="active"))
    if _column_exists(inspector, "payment_methods", "bound_at") is False:
        op.add_column("payment_methods", sa.Column("bound_at", sa.DateTime(), nullable=True))
    if _column_exists(inspector, "payment_methods", "detached_at") is False:
        op.add_column("payment_methods", sa.Column("detached_at", sa.DateTime(), nullable=True))
    if _column_exists(inspector, "payment_methods", "last_charge_at") is False:
        op.add_column("payment_methods", sa.Column("last_charge_at", sa.DateTime(), nullable=True))
    if _column_exists(inspector, "payment_methods", "last_charge_status") is False:
        op.add_column("payment_methods", sa.Column("last_charge_status", sa.String(), nullable=True))
    if _column_exists(inspector, "payment_methods", "last_charge_error") is False:
        op.add_column("payment_methods", sa.Column("last_charge_error", sa.String(), nullable=True))
    if _column_exists(inspector, "payment_methods", "binding_request_key") is False:
        op.add_column("payment_methods", sa.Column("binding_request_key", sa.String(), nullable=True))

    for table_name, column_name in [
        ("payment_methods", "status"),
        ("payment_methods", "bound_at"),
        ("payment_methods", "detached_at"),
        ("payment_methods", "last_charge_at"),
        ("payment_methods", "binding_request_key"),
    ]:
        index_name = op.f(f"ix_{table_name}_{column_name}")
        if _index_exists(inspector, table_name, index_name) is False:
            op.create_index(index_name, table_name, [column_name], unique=False)

    op.execute(
        sa.text(
            """
            UPDATE payment_methods
            SET status = COALESCE(status, 'active'),
                bound_at = COALESCE(bound_at, created_at)
            """
        )
    )
    op.alter_column("payment_methods", "status", server_default=None)

    if _column_exists(inspector, "payment", "payment_method_id") is False:
        op.add_column("payment", sa.Column("payment_method_id", postgresql.UUID(as_uuid=True), nullable=True))
    if _column_exists(inspector, "payment", "flow_kind") is False:
        op.add_column("payment", sa.Column("flow_kind", sa.String(), nullable=False, server_default="purchase"))
    if _column_exists(inspector, "payment", "renewal_failure_reason") is False:
        op.add_column("payment", sa.Column("renewal_failure_reason", sa.String(), nullable=True))
    if _column_exists(inspector, "payment", "bound_method_snapshot") is False:
        op.add_column("payment", sa.Column("bound_method_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True))

    if _index_exists(inspector, "payment", op.f("ix_payment_payment_method_id")) is False:
        op.create_index(op.f("ix_payment_payment_method_id"), "payment", ["payment_method_id"], unique=False)
    if _index_exists(inspector, "payment", op.f("ix_payment_flow_kind")) is False:
        op.create_index(op.f("ix_payment_flow_kind"), "payment", ["flow_kind"], unique=False)
    if _index_exists(inspector, "payment", op.f("ix_payment_renewal_failure_reason")) is False:
        op.create_index(op.f("ix_payment_renewal_failure_reason"), "payment", ["renewal_failure_reason"], unique=False)

    payment_fks = {fk["name"] for fk in inspector.get_foreign_keys("payment")}
    if "fk_payment_payment_method_id" not in payment_fks:
        op.create_foreign_key(
            "fk_payment_payment_method_id",
            "payment",
            "payment_methods",
            ["payment_method_id"],
            ["id"],
        )
    op.execute(sa.text("UPDATE payment SET flow_kind = 'purchase' WHERE flow_kind IS NULL"))
    op.alter_column("payment", "flow_kind", server_default=None)

    if _table_exists(inspector, "payment_binding_session") is False:
        op.create_table(
            "payment_binding_session",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("tier_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("method_type", sa.String(), nullable=False, server_default="auto"),
            sa.Column("status", sa.String(), nullable=False, server_default="pending"),
            sa.Column("request_key", sa.String(), nullable=False),
            sa.Column("payment_url", sa.String(), nullable=True),
            sa.Column("qr_payload", sa.String(), nullable=True),
            sa.Column("qr_image_svg", sa.String(), nullable=True),
            sa.Column("bank_member_id", sa.String(), nullable=True),
            sa.Column("linked_payment_method_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("error_code", sa.String(), nullable=True),
            sa.Column("error_message", sa.String(), nullable=True),
            sa.Column("bound_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["linked_payment_method_id"], ["payment_methods.id"]),
            sa.ForeignKeyConstraint(["tier_id"], ["subscription_tier.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["app_user.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("request_key"),
        )
    for column_name in [
        "user_id",
        "tier_id",
        "method_type",
        "status",
        "linked_payment_method_id",
        "error_code",
        "bound_at",
        "created_at",
    ]:
        index_name = op.f(f"ix_payment_binding_session_{column_name}")
        if _index_exists(inspector, "payment_binding_session", index_name) is False:
            op.create_index(index_name, "payment_binding_session", [column_name], unique=False)

    if _column_exists(inspector, "tier_model_limit", "daily_requests") is False:
        op.add_column("tier_model_limit", sa.Column("daily_requests", sa.Integer(), nullable=False, server_default="0"))
        op.execute(
            sa.text(
                """
                UPDATE tier_model_limit tml
                SET daily_requests = CASE
                    WHEN lower(st.name) IN ('welcoming bonus', 'welcoming_bonus', 'free')
                     AND tml.model_name IN ('gpt-5.4-nano', 'gpt-5-nano', 'gemini-3.1-flash-lite')
                    THEN CASE
                        WHEN tml.monthly_requests < 0 THEN 25
                        ELSE LEAST(GREATEST(tml.monthly_requests, 1), 25)
                    END
                    ELSE 0
                END
                FROM subscription_tier st
                WHERE st.id = tml.tier_id
                """
            )
        )
        op.alter_column("tier_model_limit", "daily_requests", server_default=None)

    if _column_exists(inspector, "user_subscription", "auto_renew_enabled") is False:
        op.add_column("user_subscription", sa.Column("auto_renew_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    if _column_exists(inspector, "user_subscription", "renewal_grace_until") is False:
        op.add_column("user_subscription", sa.Column("renewal_grace_until", sa.DateTime(), nullable=True))
    if _column_exists(inspector, "user_subscription", "last_renewal_attempt_at") is False:
        op.add_column("user_subscription", sa.Column("last_renewal_attempt_at", sa.DateTime(), nullable=True))
    if _column_exists(inspector, "user_subscription", "last_renewal_failure_reason") is False:
        op.add_column("user_subscription", sa.Column("last_renewal_failure_reason", sa.String(), nullable=True))

    for column_name in ["renewal_grace_until", "last_renewal_attempt_at", "last_renewal_failure_reason"]:
        index_name = op.f(f"ix_user_subscription_{column_name}")
        if _index_exists(inspector, "user_subscription", index_name) is False:
            op.create_index(index_name, "user_subscription", [column_name], unique=False)
    op.execute(sa.text("UPDATE user_subscription SET auto_renew_enabled = true WHERE auto_renew_enabled IS NULL"))
    op.alter_column("user_subscription", "auto_renew_enabled", server_default=None)

    if _table_exists(inspector, "document_provider_artifact") is False:
        op.create_table(
            "document_provider_artifact",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("provider", sa.String(), nullable=False, server_default="openai"),
            sa.Column("status", sa.String(), nullable=False, server_default="uploading"),
            sa.Column("external_file_id", sa.String(), nullable=True),
            sa.Column("external_index_id", sa.String(), nullable=True),
            sa.Column("error_code", sa.String(), nullable=True),
            sa.Column("error_message", sa.String(), nullable=True),
            sa.Column("indexed_at", sa.DateTime(), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["document_id"], ["user_document.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("document_id", "provider", name="uq_document_provider_artifact"),
        )
    for column_name in [
        "document_id",
        "provider",
        "status",
        "external_file_id",
        "external_index_id",
        "error_code",
        "indexed_at",
        "deleted_at",
        "created_at",
    ]:
        index_name = op.f(f"ix_document_provider_artifact_{column_name}")
        if _index_exists(inspector, "document_provider_artifact", index_name) is False:
            op.create_index(index_name, "document_provider_artifact", [column_name], unique=False)

    if _table_exists(inspector, "user_document") and _table_exists(inspector, "document_provider_artifact"):
        existing_pairs = {
            (row.document_id, row.provider)
            for row in bind.execute(
                sa.text("SELECT document_id, provider FROM document_provider_artifact")
            ).fetchall()
        }
        legacy_docs = bind.execute(
            sa.text(
                """
                SELECT id, status, openai_file_id, openai_vector_store_id, error_code, error_message, created_at, updated_at, deleted_at
                FROM user_document
                WHERE deleted_at IS NULL
                """
            )
        ).fetchall()
        backfill_rows = []
        for row in legacy_docs:
            key = (row.id, "openai")
            if key in existing_pairs:
                continue
            if not (row.openai_file_id or row.openai_vector_store_id or row.status in ("uploading", "processing", "ready", "failed", "delete_queued")):
                continue
            backfill_rows.append(
                {
                    "id": uuid.uuid4(),
                    "document_id": row.id,
                    "provider": "openai",
                    "status": row.status,
                    "external_file_id": row.openai_file_id,
                    "external_index_id": row.openai_vector_store_id,
                    "error_code": row.error_code,
                    "error_message": row.error_message,
                    "indexed_at": row.updated_at if row.status == "ready" else None,
                    "deleted_at": row.deleted_at,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                }
            )
        if backfill_rows:
            artifact_table = sa.table(
                "document_provider_artifact",
                sa.column("id", postgresql.UUID(as_uuid=True)),
                sa.column("document_id", postgresql.UUID(as_uuid=True)),
                sa.column("provider", sa.String()),
                sa.column("status", sa.String()),
                sa.column("external_file_id", sa.String()),
                sa.column("external_index_id", sa.String()),
                sa.column("error_code", sa.String()),
                sa.column("error_message", sa.String()),
                sa.column("indexed_at", sa.DateTime()),
                sa.column("deleted_at", sa.DateTime()),
                sa.column("created_at", sa.DateTime()),
                sa.column("updated_at", sa.DateTime()),
            )
            op.bulk_insert(artifact_table, backfill_rows)

    if _table_exists(inspector, "image_quality_pricing"):
        op.execute(
            sa.text(
                """
                UPDATE image_quality_pricing
                SET is_active = false
                WHERE image_model IN ('gemini-2.5-flash-image', 'gemini-3.1-flash-image-preview', 'gemini-3-pro-image-preview')
                  AND quality IN ('low', 'medium', 'high')
                """
            )
        )
        op.execute(
            sa.text(
                """
                UPDATE image_quality_pricing
                SET is_active = true
                WHERE image_model IN ('gemini-2.5-flash-image', 'gemini-3.1-flash-image-preview', 'gemini-3-pro-image-preview')
                  AND quality IN ('512', '1k', '2k')
                """
            )
        )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _table_exists(inspector, "document_provider_artifact"):
        for column_name in [
            "created_at",
            "deleted_at",
            "indexed_at",
            "error_code",
            "external_index_id",
            "external_file_id",
            "status",
            "provider",
            "document_id",
        ]:
            index_name = op.f(f"ix_document_provider_artifact_{column_name}")
            if _index_exists(inspector, "document_provider_artifact", index_name):
                op.drop_index(index_name, table_name="document_provider_artifact")
        op.drop_table("document_provider_artifact")

    if _column_exists(inspector, "user_subscription", "last_renewal_failure_reason"):
        if _index_exists(inspector, "user_subscription", op.f("ix_user_subscription_last_renewal_failure_reason")):
            op.drop_index(op.f("ix_user_subscription_last_renewal_failure_reason"), table_name="user_subscription")
        op.drop_column("user_subscription", "last_renewal_failure_reason")
    if _column_exists(inspector, "user_subscription", "last_renewal_attempt_at"):
        if _index_exists(inspector, "user_subscription", op.f("ix_user_subscription_last_renewal_attempt_at")):
            op.drop_index(op.f("ix_user_subscription_last_renewal_attempt_at"), table_name="user_subscription")
        op.drop_column("user_subscription", "last_renewal_attempt_at")
    if _column_exists(inspector, "user_subscription", "renewal_grace_until"):
        if _index_exists(inspector, "user_subscription", op.f("ix_user_subscription_renewal_grace_until")):
            op.drop_index(op.f("ix_user_subscription_renewal_grace_until"), table_name="user_subscription")
        op.drop_column("user_subscription", "renewal_grace_until")
    if _column_exists(inspector, "user_subscription", "auto_renew_enabled"):
        op.drop_column("user_subscription", "auto_renew_enabled")

    if _column_exists(inspector, "tier_model_limit", "daily_requests"):
        op.drop_column("tier_model_limit", "daily_requests")

    if _table_exists(inspector, "payment_binding_session"):
        for column_name in [
            "created_at",
            "bound_at",
            "error_code",
            "linked_payment_method_id",
            "status",
            "method_type",
            "tier_id",
            "user_id",
        ]:
            index_name = op.f(f"ix_payment_binding_session_{column_name}")
            if _index_exists(inspector, "payment_binding_session", index_name):
                op.drop_index(index_name, table_name="payment_binding_session")
        op.drop_table("payment_binding_session")

    if _column_exists(inspector, "payment", "bound_method_snapshot"):
        op.drop_column("payment", "bound_method_snapshot")
    if _column_exists(inspector, "payment", "renewal_failure_reason"):
        if _index_exists(inspector, "payment", op.f("ix_payment_renewal_failure_reason")):
            op.drop_index(op.f("ix_payment_renewal_failure_reason"), table_name="payment")
        op.drop_column("payment", "renewal_failure_reason")
    if _column_exists(inspector, "payment", "flow_kind"):
        if _index_exists(inspector, "payment", op.f("ix_payment_flow_kind")):
            op.drop_index(op.f("ix_payment_flow_kind"), table_name="payment")
        op.drop_column("payment", "flow_kind")
    if _column_exists(inspector, "payment", "payment_method_id"):
        if _index_exists(inspector, "payment", op.f("ix_payment_payment_method_id")):
            op.drop_index(op.f("ix_payment_payment_method_id"), table_name="payment")
        payment_fks = {fk["name"] for fk in inspector.get_foreign_keys("payment")}
        if "fk_payment_payment_method_id" in payment_fks:
            op.drop_constraint("fk_payment_payment_method_id", "payment", type_="foreignkey")
        op.drop_column("payment", "payment_method_id")

    for column_name in [
        "binding_request_key",
        "last_charge_at",
        "detached_at",
        "bound_at",
        "status",
    ]:
        index_name = op.f(f"ix_payment_methods_{column_name}")
        if _index_exists(inspector, "payment_methods", index_name):
            op.drop_index(index_name, table_name="payment_methods")
    for column_name in [
        "binding_request_key",
        "last_charge_error",
        "last_charge_status",
        "last_charge_at",
        "detached_at",
        "bound_at",
        "status",
    ]:
        if _column_exists(inspector, "payment_methods", column_name):
            op.drop_column("payment_methods", column_name)

    if _column_exists(inspector, "app_user", "default_document_provider"):
        op.drop_column("app_user", "default_document_provider")
