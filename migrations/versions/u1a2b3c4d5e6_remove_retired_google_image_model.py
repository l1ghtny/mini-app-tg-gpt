"""remove retired google image model

Revision ID: u1a2b3c4d5e6
Revises: dca1ce1aecc2
Create Date: 2026-06-09 12:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "u1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "dca1ce1aecc2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


LEGACY_IMAGE_MODEL = "gemini-2.5-flash-image"
CANONICAL_IMAGE_MODEL = "gemini-3.1-flash-image-preview"


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return inspector.has_table(table_name)


def _execute(bind: sa.Connection, sql: str, params: dict[str, str]) -> None:
    bind.execute(sa.text(sql), params)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    params = {
        "legacy_model": LEGACY_IMAGE_MODEL,
        "canonical_model": CANONICAL_IMAGE_MODEL,
    }

    if _table_exists(inspector, "app_user"):
        _execute(
            bind,
            """
            UPDATE app_user
            SET default_image_model = :canonical_model
            WHERE default_image_model = :legacy_model
            """,
            params,
        )

    if _table_exists(inspector, "conversation"):
        _execute(
            bind,
            """
            UPDATE conversation
            SET image_model = :canonical_model
            WHERE image_model = :legacy_model
            """,
            params,
        )

    if _table_exists(inspector, "request_ledger"):
        _execute(
            bind,
            """
            UPDATE request_ledger
            SET model_name = :canonical_model
            WHERE model_name = :legacy_model
            """,
            params,
        )

    if _table_exists(inspector, "tokenusage"):
        _execute(
            bind,
            """
            UPDATE tokenusage
            SET model_name = :canonical_model
            WHERE model_name = :legacy_model
            """,
            params,
        )

    if _table_exists(inspector, "tier_image_model_limit"):
        _execute(
            bind,
            """
            UPDATE tier_image_model_limit AS target
            SET monthly_requests = GREATEST(target.monthly_requests, source.monthly_requests)
            FROM tier_image_model_limit AS source
            WHERE source.image_model = :legacy_model
              AND target.image_model = :canonical_model
              AND target.tier_id = source.tier_id
            """,
            params,
        )
        _execute(
            bind,
            """
            UPDATE tier_image_model_limit AS source
            SET image_model = :canonical_model
            WHERE source.image_model = :legacy_model
              AND NOT EXISTS (
                  SELECT 1
                  FROM tier_image_model_limit AS target
                  WHERE target.tier_id = source.tier_id
                    AND target.image_model = :canonical_model
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

    if _table_exists(inspector, "usage_pack_image_model_limit"):
        _execute(
            bind,
            """
            UPDATE usage_pack_image_model_limit AS target
            SET credit_amount = GREATEST(target.credit_amount, source.credit_amount)
            FROM usage_pack_image_model_limit AS source
            WHERE source.image_model = :legacy_model
              AND target.image_model = :canonical_model
              AND target.pack_id = source.pack_id
            """,
            params,
        )
        _execute(
            bind,
            """
            UPDATE usage_pack_image_model_limit AS source
            SET image_model = :canonical_model
            WHERE source.image_model = :legacy_model
              AND NOT EXISTS (
                  SELECT 1
                  FROM usage_pack_image_model_limit AS target
                  WHERE target.pack_id = source.pack_id
                    AND target.image_model = :canonical_model
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

    if _table_exists(inspector, "image_model_catalog"):
        _execute(
            bind,
            """
            DELETE FROM image_model_catalog
            WHERE model_name = :legacy_model
            """,
            params,
        )

    if _table_exists(inspector, "image_quality_pricing"):
        _execute(
            bind,
            """
            DELETE FROM image_quality_pricing
            WHERE image_model = :legacy_model
            """,
            params,
        )


def downgrade() -> None:
    # This cleanup intentionally leaves the canonical Google image model in place.
    # Re-introducing the retired model would require restoring deleted catalog and
    # entitlement rows with business-specific values, so the downgrade is a no-op.
    pass
