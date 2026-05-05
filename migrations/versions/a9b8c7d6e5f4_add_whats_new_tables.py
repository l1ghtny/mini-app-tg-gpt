"""add whats new tables

Revision ID: a9b8c7d6e5f4
Revises: e1f2a3b4c5d6
Create Date: 2026-05-05 12:35:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "a9b8c7d6e5f4"
down_revision: Union[str, Sequence[str], None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("whats_new_item"):
        op.create_table(
            "whats_new_item",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("title_en", sa.String(), nullable=False),
            sa.Column("title_ru", sa.String(), nullable=True),
            sa.Column("body_en", sa.String(), nullable=False),
            sa.Column("body_ru", sa.String(), nullable=True),
            sa.Column("icon", sa.String(), nullable=True),
            sa.Column("image_url", sa.String(), nullable=True),
            sa.Column("cta_label_en", sa.String(), nullable=True),
            sa.Column("cta_label_ru", sa.String(), nullable=True),
            sa.Column("cta_kind", sa.String(), nullable=True),
            sa.Column("cta_value", sa.String(), nullable=True),
            sa.Column(
                "audience_plans",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column("min_app_version", sa.String(), nullable=True),
            sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("starts_at", sa.DateTime(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("published_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
            sa.CheckConstraint(
                "kind IN ('feature','improvement','fix','announcement','promo')",
                name="ck_whats_new_item_kind",
            ),
            sa.CheckConstraint(
                "cta_kind IS NULL OR cta_kind IN ('open_settings','open_subscription','open_url','dismiss')",
                name="ck_whats_new_item_cta_kind",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_whats_new_item_kind"), "whats_new_item", ["kind"], unique=False)
        op.create_index(op.f("ix_whats_new_item_pinned"), "whats_new_item", ["pinned"], unique=False)
        op.create_index(op.f("ix_whats_new_item_starts_at"), "whats_new_item", ["starts_at"], unique=False)
        op.create_index(op.f("ix_whats_new_item_expires_at"), "whats_new_item", ["expires_at"], unique=False)
        op.create_index(op.f("ix_whats_new_item_published_at"), "whats_new_item", ["published_at"], unique=False)
        op.create_index(op.f("ix_whats_new_item_is_active"), "whats_new_item", ["is_active"], unique=False)
        op.create_index(op.f("ix_whats_new_item_created_at"), "whats_new_item", ["created_at"], unique=False)

    if not inspector.has_table("user_whats_new_state"):
        op.create_table(
            "user_whats_new_state",
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("seen_up_to", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["user_id"], ["app_user.id"]),
            sa.PrimaryKeyConstraint("user_id"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("user_whats_new_state"):
        op.drop_table("user_whats_new_state")

    if inspector.has_table("whats_new_item"):
        op.drop_index(op.f("ix_whats_new_item_created_at"), table_name="whats_new_item")
        op.drop_index(op.f("ix_whats_new_item_is_active"), table_name="whats_new_item")
        op.drop_index(op.f("ix_whats_new_item_published_at"), table_name="whats_new_item")
        op.drop_index(op.f("ix_whats_new_item_expires_at"), table_name="whats_new_item")
        op.drop_index(op.f("ix_whats_new_item_starts_at"), table_name="whats_new_item")
        op.drop_index(op.f("ix_whats_new_item_pinned"), table_name="whats_new_item")
        op.drop_index(op.f("ix_whats_new_item_kind"), table_name="whats_new_item")
        op.drop_table("whats_new_item")

