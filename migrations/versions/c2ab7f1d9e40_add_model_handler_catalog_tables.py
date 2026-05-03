"""add model handler catalog tables

Revision ID: c2ab7f1d9e40
Revises: a4c6f8d2e1b0
Create Date: 2026-05-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = "c2ab7f1d9e40"
down_revision: Union[str, Sequence[str], None] = "a4c6f8d2e1b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {col["name"] for col in inspector.get_columns(table_name)}


def _index_exists(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return index_name in {idx["name"] for idx in inspector.get_indexes(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("text_model_catalog"):
        op.create_table(
            "text_model_catalog",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("provider", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("model_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("display_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("display_name_ru", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("tagline", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("tagline_ru", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("description_ru", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("best_for", JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("best_for_ru", JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("not_great_for", JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("not_great_for_ru", JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("speed", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("intelligence", sa.Integer(), nullable=True),
            sa.Column("context_window", sa.Integer(), nullable=True),
            sa.Column("supports", JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("tier_required", JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("badges", JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("credit_cost_hint", sa.Numeric(18, 6), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("sort_index", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("provider", "model_name", name="uq_text_model_catalog_provider_model"),
        )

    inspector = sa.inspect(bind)
    if inspector.has_table("text_model_catalog"):
        if not _index_exists(inspector, "text_model_catalog", op.f("ix_text_model_catalog_provider")):
            op.create_index(op.f("ix_text_model_catalog_provider"), "text_model_catalog", ["provider"], unique=False)
        if not _index_exists(inspector, "text_model_catalog", op.f("ix_text_model_catalog_model_name")):
            op.create_index(op.f("ix_text_model_catalog_model_name"), "text_model_catalog", ["model_name"], unique=False)
        if not _index_exists(inspector, "text_model_catalog", "ix_text_model_catalog_active_sort"):
            op.create_index("ix_text_model_catalog_active_sort", "text_model_catalog", ["is_active", "sort_index"], unique=False)

    if not inspector.has_table("image_model_catalog"):
        op.create_table(
            "image_model_catalog",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("provider", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("model_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("display_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("display_name_ru", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("tagline", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("tagline_ru", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("description_ru", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("best_for", JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("best_for_ru", JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("speed", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("tier_required", JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("badges", JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("sort_index", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("provider", "model_name", name="uq_image_model_catalog_provider_model"),
        )

    inspector = sa.inspect(bind)
    if inspector.has_table("image_model_catalog"):
        if not _index_exists(inspector, "image_model_catalog", op.f("ix_image_model_catalog_provider")):
            op.create_index(op.f("ix_image_model_catalog_provider"), "image_model_catalog", ["provider"], unique=False)
        if not _index_exists(inspector, "image_model_catalog", op.f("ix_image_model_catalog_model_name")):
            op.create_index(op.f("ix_image_model_catalog_model_name"), "image_model_catalog", ["model_name"], unique=False)
        if not _index_exists(inspector, "image_model_catalog", "ix_image_model_catalog_active_sort"):
            op.create_index("ix_image_model_catalog_active_sort", "image_model_catalog", ["is_active", "sort_index"], unique=False)

    if inspector.has_table("image_quality_pricing") and not _column_exists(inspector, "image_quality_pricing", "description_ru"):
        op.add_column(
            "image_quality_pricing",
            sa.Column("description_ru", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("image_quality_pricing") and _column_exists(inspector, "image_quality_pricing", "description_ru"):
        op.drop_column("image_quality_pricing", "description_ru")

    if inspector.has_table("image_model_catalog"):
        image_indexes = {idx["name"] for idx in inspector.get_indexes("image_model_catalog")}
        if "ix_image_model_catalog_active_sort" in image_indexes:
            op.drop_index("ix_image_model_catalog_active_sort", table_name="image_model_catalog")
        if op.f("ix_image_model_catalog_model_name") in image_indexes:
            op.drop_index(op.f("ix_image_model_catalog_model_name"), table_name="image_model_catalog")
        if op.f("ix_image_model_catalog_provider") in image_indexes:
            op.drop_index(op.f("ix_image_model_catalog_provider"), table_name="image_model_catalog")
        op.drop_table("image_model_catalog")

    inspector = sa.inspect(bind)
    if inspector.has_table("text_model_catalog"):
        text_indexes = {idx["name"] for idx in inspector.get_indexes("text_model_catalog")}
        if "ix_text_model_catalog_active_sort" in text_indexes:
            op.drop_index("ix_text_model_catalog_active_sort", table_name="text_model_catalog")
        if op.f("ix_text_model_catalog_model_name") in text_indexes:
            op.drop_index(op.f("ix_text_model_catalog_model_name"), table_name="text_model_catalog")
        if op.f("ix_text_model_catalog_provider") in text_indexes:
            op.drop_index(op.f("ix_text_model_catalog_provider"), table_name="text_model_catalog")
        op.drop_table("text_model_catalog")
