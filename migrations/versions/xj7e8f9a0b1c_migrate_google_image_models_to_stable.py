"""Migrate Google image models from retired previews to stable endpoints.

Revision ID: xj7e8f9a0b1c
Revises: xi6d7e8f9a0b
Create Date: 2026-08-19 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "xj7e8f9a0b1c"
down_revision: Union[str, Sequence[str], None] = "xi6d7e8f9a0b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MODEL_REPLACEMENTS = (
    ("gemini-3.1-flash-image-preview", "gemini-3.1-flash-image"),
    ("gemini-3-pro-image-preview", "gemini-3-pro-image"),
    ("gemini-2.5-flash-image", "gemini-3.1-flash-image"),
)


def _execute(bind: sa.Connection, sql: str, params: dict[str, str]) -> None:
    bind.execute(sa.text(sql), params)


def _migrate_model(
    bind: sa.Connection,
    inspector: sa.Inspector,
    legacy_model: str,
    stable_model: str,
) -> None:
    params = {"legacy_model": legacy_model, "stable_model": stable_model}

    if inspector.has_table("app_user"):
        _execute(
            bind,
            """
            UPDATE app_user
            SET default_image_model = :stable_model
            WHERE default_image_model = :legacy_model
            """,
            params,
        )

    if inspector.has_table("conversation"):
        conversation_columns = {
            column["name"] for column in inspector.get_columns("conversation")
        }
        if "image_size" in conversation_columns and stable_model == "gemini-3-pro-image":
            _execute(
                bind,
                """
                UPDATE conversation
                SET image_size = '1k'
                WHERE image_model IN (:legacy_model, :stable_model)
                  AND lower(image_size) = '512'
                """,
                params,
            )
        _execute(
            bind,
            """
            UPDATE conversation
            SET image_model = :stable_model
            WHERE image_model = :legacy_model
            """,
            params,
        )

    for table_name in ("request_ledger", "tokenusage"):
        if inspector.has_table(table_name):
            _execute(
                bind,
                f"""
                UPDATE {table_name}
                SET model_name = :stable_model
                WHERE model_name = :legacy_model
                """,
                params,
            )

    if inspector.has_table("tier_image_model_limit"):
        _execute(
            bind,
            """
            UPDATE tier_image_model_limit AS target
            SET monthly_requests = CASE
                WHEN target.monthly_requests < 0 OR source.monthly_requests < 0 THEN -1
                ELSE GREATEST(target.monthly_requests, source.monthly_requests)
            END
            FROM tier_image_model_limit AS source
            WHERE source.image_model = :legacy_model
              AND target.image_model = :stable_model
              AND target.tier_id = source.tier_id
            """,
            params,
        )
        _execute(
            bind,
            """
            UPDATE tier_image_model_limit AS source
            SET image_model = :stable_model
            WHERE source.image_model = :legacy_model
              AND NOT EXISTS (
                  SELECT 1
                  FROM tier_image_model_limit AS target
                  WHERE target.tier_id = source.tier_id
                    AND target.image_model = :stable_model
              )
            """,
            params,
        )
        _execute(
            bind,
            """
            DELETE FROM tier_image_model_limit
            WHERE image_model = :legacy_model
            """,
            params,
        )

    if inspector.has_table("usage_pack_image_model_limit"):
        _execute(
            bind,
            """
            UPDATE usage_pack_image_model_limit AS target
            SET credit_amount = GREATEST(target.credit_amount, source.credit_amount)
            FROM usage_pack_image_model_limit AS source
            WHERE source.image_model = :legacy_model
              AND target.image_model = :stable_model
              AND target.pack_id = source.pack_id
            """,
            params,
        )
        _execute(
            bind,
            """
            UPDATE usage_pack_image_model_limit AS source
            SET image_model = :stable_model
            WHERE source.image_model = :legacy_model
              AND NOT EXISTS (
                  SELECT 1
                  FROM usage_pack_image_model_limit AS target
                  WHERE target.pack_id = source.pack_id
                    AND target.image_model = :stable_model
              )
            """,
            params,
        )
        _execute(
            bind,
            """
            DELETE FROM usage_pack_image_model_limit
            WHERE image_model = :legacy_model
            """,
            params,
        )

    if inspector.has_table("image_model_catalog"):
        _execute(
            bind,
            """
            UPDATE image_model_catalog AS source
            SET model_name = :stable_model,
                is_active = true,
                updated_at = now()
            WHERE source.provider = 'google'
              AND source.model_name = :legacy_model
              AND NOT EXISTS (
                  SELECT 1
                  FROM image_model_catalog AS target
                  WHERE target.provider = 'google'
                    AND target.model_name = :stable_model
              )
            """,
            params,
        )
        _execute(
            bind,
            """
            UPDATE image_model_catalog
            SET is_active = true,
                updated_at = now()
            WHERE provider = 'google'
              AND model_name = :stable_model
            """,
            params,
        )
        _execute(
            bind,
            """
            DELETE FROM image_model_catalog
            WHERE provider = 'google'
              AND model_name = :legacy_model
            """,
            params,
        )

    if inspector.has_table("image_quality_pricing"):
        _execute(
            bind,
            """
            UPDATE image_quality_pricing AS source
            SET image_model = :stable_model
            WHERE source.image_model = :legacy_model
              AND NOT EXISTS (
                  SELECT 1
                  FROM image_quality_pricing AS target
                  WHERE target.image_model = :stable_model
                    AND target.quality = source.quality
              )
            """,
            params,
        )
        _execute(
            bind,
            """
            DELETE FROM image_quality_pricing
            WHERE image_model = :legacy_model
            """,
            params,
        )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for legacy_model, stable_model in MODEL_REPLACEMENTS:
        _migrate_model(bind, inspector, legacy_model, stable_model)
    if inspector.has_table("image_quality_pricing"):
        _execute(
            bind,
            """
            DELETE FROM image_quality_pricing
            WHERE image_model = 'gemini-3-pro-image'
              AND lower(quality) = '512'
            """,
            {},
        )


def downgrade() -> None:
    # Retired provider endpoints must not be restored by a database downgrade.
    pass
