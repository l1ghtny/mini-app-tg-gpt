"""upgrade text models to gpt 5.4 and refresh tier limits

Revision ID: d7e8f9a0b1c2
Revises: c4d5e6f7a8b9
Create Date: 2026-05-04 10:15:00.000000
"""

from __future__ import annotations

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert


# revision identifiers, used by Alembic.
revision: str = "d7e8f9a0b1c2"
down_revision: Union[str, Sequence[str], None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SEED_NAMESPACE = uuid.UUID("5f03d374-0e80-4c3e-9ad9-0400dbfef734")


def _stable_uuid(label: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, label)


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {col["name"] for col in inspector.get_columns(table_name)}


def _merge_pro_into_advanced(bind, inspector: sa.Inspector) -> None:
    if not inspector.has_table("subscription_tier"):
        return

    advanced_id = bind.execute(
        sa.text("SELECT id FROM subscription_tier WHERE lower(name) = 'advanced' LIMIT 1")
    ).scalar()
    pro_id = bind.execute(
        sa.text("SELECT id FROM subscription_tier WHERE lower(name) = 'pro' LIMIT 1")
    ).scalar()

    if not pro_id:
        return

    if not advanced_id:
        op.execute(sa.text("UPDATE subscription_tier SET name = 'advanced', name_ru = 'advanced' WHERE id = :pro_id"))
        return

    if inspector.has_table("tier_model_limit"):
        op.execute(sa.text(
            """
            UPDATE tier_model_limit dst
            SET monthly_requests = GREATEST(dst.monthly_requests, src.monthly_requests)
            FROM tier_model_limit src
            WHERE dst.tier_id = :advanced_id
              AND src.tier_id = :pro_id
              AND dst.model_name = src.model_name
            """
        ).bindparams(advanced_id=advanced_id, pro_id=pro_id))
        op.execute(sa.text(
            """
            DELETE FROM tier_model_limit src
            USING tier_model_limit dst
            WHERE src.tier_id = :pro_id
              AND dst.tier_id = :advanced_id
              AND dst.model_name = src.model_name
            """
        ).bindparams(advanced_id=advanced_id, pro_id=pro_id))
        op.execute(sa.text("UPDATE tier_model_limit SET tier_id = :advanced_id WHERE tier_id = :pro_id")
                   .bindparams(advanced_id=advanced_id, pro_id=pro_id))

    if inspector.has_table("tier_image_model_limit"):
        op.execute(sa.text(
            """
            UPDATE tier_image_model_limit dst
            SET monthly_requests = GREATEST(dst.monthly_requests, src.monthly_requests)
            FROM tier_image_model_limit src
            WHERE dst.tier_id = :advanced_id
              AND src.tier_id = :pro_id
              AND dst.image_model = src.image_model
            """
        ).bindparams(advanced_id=advanced_id, pro_id=pro_id))
        op.execute(sa.text(
            """
            DELETE FROM tier_image_model_limit src
            USING tier_image_model_limit dst
            WHERE src.tier_id = :pro_id
              AND dst.tier_id = :advanced_id
              AND dst.image_model = src.image_model
            """
        ).bindparams(advanced_id=advanced_id, pro_id=pro_id))
        op.execute(sa.text("UPDATE tier_image_model_limit SET tier_id = :advanced_id WHERE tier_id = :pro_id")
                   .bindparams(advanced_id=advanced_id, pro_id=pro_id))

    if inspector.has_table("tier_image_quality_limit"):
        op.execute(sa.text(
            """
            DELETE FROM tier_image_quality_limit src
            USING tier_image_quality_limit dst
            WHERE src.tier_id = :pro_id
              AND dst.tier_id = :advanced_id
              AND dst.quality = src.quality
            """
        ).bindparams(advanced_id=advanced_id, pro_id=pro_id))
        op.execute(sa.text("UPDATE tier_image_quality_limit SET tier_id = :advanced_id WHERE tier_id = :pro_id")
                   .bindparams(advanced_id=advanced_id, pro_id=pro_id))

    if inspector.has_table("user_subscription"):
        op.execute(sa.text("UPDATE user_subscription SET tier_id = :advanced_id WHERE tier_id = :pro_id")
                   .bindparams(advanced_id=advanced_id, pro_id=pro_id))

    if inspector.has_table("request_ledger") and _column_exists(inspector, "request_ledger", "tier_id"):
        op.execute(sa.text("UPDATE request_ledger SET tier_id = :advanced_id WHERE tier_id = :pro_id")
                   .bindparams(advanced_id=advanced_id, pro_id=pro_id))

    if inspector.has_table("access_code") and _column_exists(inspector, "access_code", "tier_id"):
        op.execute(sa.text("UPDATE access_code SET tier_id = :advanced_id WHERE tier_id = :pro_id")
                   .bindparams(advanced_id=advanced_id, pro_id=pro_id))

    if inspector.has_table("access_code_discounts") and _column_exists(inspector, "access_code_discounts", "tier_id"):
        op.execute(sa.text("UPDATE access_code_discounts SET tier_id = :advanced_id WHERE tier_id = :pro_id")
                   .bindparams(advanced_id=advanced_id, pro_id=pro_id))

    if inspector.has_table("usertierdiscount") and _column_exists(inspector, "usertierdiscount", "tier_id"):
        op.execute(sa.text("UPDATE usertierdiscount SET tier_id = :advanced_id WHERE tier_id = :pro_id")
                   .bindparams(advanced_id=advanced_id, pro_id=pro_id))

    op.execute(sa.text("DELETE FROM subscription_tier WHERE id = :pro_id").bindparams(pro_id=pro_id))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("conversation"):
        op.execute(sa.text("UPDATE conversation SET model = 'gpt-5.4-nano' WHERE model = 'gpt-5-nano'"))
        op.execute(sa.text("UPDATE conversation SET model = 'gpt-5.4-mini' WHERE model = 'gpt-5-mini'"))
        op.execute(sa.text("UPDATE conversation SET model = 'gpt-5.4' WHERE model = 'gpt-5.2'"))
        op.alter_column(
            "conversation",
            "model",
            existing_type=sa.String(),
            server_default=sa.text("'gpt-5.4-nano'::character varying"),
        )

    if inspector.has_table("request_ledger"):
        op.execute(sa.text("UPDATE request_ledger SET model_name = 'gpt-5.4-nano' WHERE model_name = 'gpt-5-nano'"))
        op.execute(sa.text("UPDATE request_ledger SET model_name = 'gpt-5.4-mini' WHERE model_name = 'gpt-5-mini'"))
        op.execute(sa.text("UPDATE request_ledger SET model_name = 'gpt-5.4' WHERE model_name = 'gpt-5.2'"))

    if inspector.has_table("tier_model_limit"):
        op.execute(sa.text(
            """
            UPDATE tier_model_limit t
            SET model_name = 'gpt-5.4-nano'
            WHERE t.model_name = 'gpt-5-nano'
              AND NOT EXISTS (
                SELECT 1 FROM tier_model_limit x
                WHERE x.tier_id = t.tier_id AND x.model_name = 'gpt-5.4-nano'
              )
            """
        ))
        op.execute(sa.text(
            """
            UPDATE tier_model_limit t
            SET model_name = 'gpt-5.4-mini'
            WHERE t.model_name = 'gpt-5-mini'
              AND NOT EXISTS (
                SELECT 1 FROM tier_model_limit x
                WHERE x.tier_id = t.tier_id AND x.model_name = 'gpt-5.4-mini'
              )
            """
        ))
        op.execute(sa.text(
            """
            UPDATE tier_model_limit t
            SET model_name = 'gpt-5.4'
            WHERE t.model_name = 'gpt-5.2'
              AND NOT EXISTS (
                SELECT 1 FROM tier_model_limit x
                WHERE x.tier_id = t.tier_id AND x.model_name = 'gpt-5.4'
              )
            """
        ))
        op.execute(sa.text("DELETE FROM tier_model_limit WHERE model_name IN ('gpt-5-nano', 'gpt-5-mini', 'gpt-5.2')"))

    if inspector.has_table("usage_pack_model_limit"):
        op.execute(sa.text(
            """
            UPDATE usage_pack_model_limit t
            SET model_name = 'gpt-5.4-nano'
            WHERE t.model_name = 'gpt-5-nano'
              AND NOT EXISTS (
                SELECT 1 FROM usage_pack_model_limit x
                WHERE x.pack_id = t.pack_id AND x.model_name = 'gpt-5.4-nano'
              )
            """
        ))
        op.execute(sa.text(
            """
            UPDATE usage_pack_model_limit t
            SET model_name = 'gpt-5.4-mini'
            WHERE t.model_name = 'gpt-5-mini'
              AND NOT EXISTS (
                SELECT 1 FROM usage_pack_model_limit x
                WHERE x.pack_id = t.pack_id AND x.model_name = 'gpt-5.4-mini'
              )
            """
        ))
        op.execute(sa.text(
            """
            UPDATE usage_pack_model_limit t
            SET model_name = 'gpt-5.4'
            WHERE t.model_name = 'gpt-5.2'
              AND NOT EXISTS (
                SELECT 1 FROM usage_pack_model_limit x
                WHERE x.pack_id = t.pack_id AND x.model_name = 'gpt-5.4'
              )
            """
        ))
        op.execute(sa.text("DELETE FROM usage_pack_model_limit WHERE model_name IN ('gpt-5-nano', 'gpt-5-mini', 'gpt-5.2')"))

    if inspector.has_table("text_model_catalog"):
        op.execute(sa.text(
            """
            UPDATE text_model_catalog t
            SET model_name = 'gpt-5.4-nano', updated_at = now()
            WHERE t.provider = 'OpenAI'
              AND t.model_name = 'gpt-5-nano'
              AND NOT EXISTS (
                SELECT 1 FROM text_model_catalog x
                WHERE x.provider = t.provider AND x.model_name = 'gpt-5.4-nano'
              )
            """
        ))
        op.execute(sa.text(
            """
            UPDATE text_model_catalog t
            SET model_name = 'gpt-5.4-mini', updated_at = now()
            WHERE t.provider = 'OpenAI'
              AND t.model_name = 'gpt-5-mini'
              AND NOT EXISTS (
                SELECT 1 FROM text_model_catalog x
                WHERE x.provider = t.provider AND x.model_name = 'gpt-5.4-mini'
              )
            """
        ))
        op.execute(sa.text(
            """
            UPDATE text_model_catalog t
            SET model_name = 'gpt-5.4', updated_at = now()
            WHERE t.provider = 'OpenAI'
              AND t.model_name = 'gpt-5.2'
              AND NOT EXISTS (
                SELECT 1 FROM text_model_catalog x
                WHERE x.provider = t.provider AND x.model_name = 'gpt-5.4'
              )
            """
        ))
        op.execute(sa.text(
            """
            UPDATE text_model_catalog
            SET model_name = 'gpt-5.4-nano', display_name = 'Fast', display_name_ru = 'Быстрый', updated_at = now()
            WHERE provider = 'OpenAI' AND model_name = 'gpt-5.4-nano'
            """
        ))
        op.execute(sa.text(
            """
            UPDATE text_model_catalog
            SET model_name = 'gpt-5.4-mini', display_name = 'Smart', display_name_ru = 'Умный', updated_at = now()
            WHERE provider = 'OpenAI' AND model_name = 'gpt-5.4-mini'
            """
        ))
        op.execute(sa.text(
            """
            UPDATE text_model_catalog
            SET model_name = 'gpt-5.4', display_name = 'Balanced', display_name_ru = 'Сбалансированный', updated_at = now()
            WHERE provider = 'OpenAI' AND model_name = 'gpt-5.4'
            """
        ))
        op.execute(sa.text(
            """
            UPDATE text_model_catalog
            SET is_active = FALSE, updated_at = now()
            WHERE provider = 'OpenAI' AND model_name IN ('gpt-5-nano', 'gpt-5-mini', 'gpt-5.2')
            """
        ))

    if inspector.has_table("subscription_tier"):
        _merge_pro_into_advanced(bind, inspector)
        op.execute(sa.text("UPDATE subscription_tier SET price_cents = 490 WHERE lower(name) = 'basic'"))
        op.execute(sa.text("UPDATE subscription_tier SET price_cents = 1490 WHERE lower(name) = 'advanced'"))
        op.execute(sa.text("UPDATE subscription_tier SET price_cents = 2490 WHERE lower(name) = 'premium'"))

    if inspector.has_table("text_model_catalog"):
        op.execute(sa.text(
            """
            UPDATE text_model_catalog
            SET tier_required = jsonb_set(tier_required, '{slug}', '"advanced"', false), updated_at = now()
            WHERE tier_required IS NOT NULL AND tier_required->>'slug' = 'pro'
            """
        ))

    if inspector.has_table("image_model_catalog"):
        op.execute(sa.text(
            """
            UPDATE image_model_catalog
            SET tier_required = jsonb_set(tier_required, '{slug}', '"advanced"', false), updated_at = now()
            WHERE tier_required IS NOT NULL AND tier_required->>'slug' = 'pro'
            """
        ))

    if not (inspector.has_table("subscription_tier") and inspector.has_table("tier_model_limit")):
        return

    subscription_tier = sa.Table(
        "subscription_tier",
        sa.MetaData(),
        sa.Column("id", sa.Uuid()),
        sa.Column("name", sa.String()),
    )
    tier_model_limit = sa.Table(
        "tier_model_limit",
        sa.MetaData(),
        sa.Column("id", sa.Uuid()),
        sa.Column("tier_id", sa.Uuid()),
        sa.Column("model_name", sa.String()),
        sa.Column("monthly_requests", sa.Integer()),
    )

    tiers = bind.execute(
        sa.select(subscription_tier.c.id, subscription_tier.c.name)
    ).all()
    if not tiers:
        return

    by_name = {str((row.name or "")).strip().lower(): row.id for row in tiers}
    target_tiers: dict[str, dict[str, int]] = {
        "basic": {
            "gpt-5.4-nano": -1,
            "gpt-5.4-mini": 250,
            "gpt-5.4": 15,
            "gpt-5.5": 3,
        },
        "advanced": {
            "gpt-5.4-nano": -1,
            "gpt-5.4-mini": -1,
            "gpt-5.4": 250,
            "gpt-5.5": 15,
        },
        "premium": {
            "gpt-5.4-nano": -1,
            "gpt-5.4-mini": -1,
            "gpt-5.4": 1000,
            "gpt-5.5": 100,
        },
    }

    for tier_name, limits in target_tiers.items():
        tier_id = by_name.get(tier_name)
        if not tier_id:
            continue
        for model_name, monthly_requests in limits.items():
            insert_stmt = pg_insert(tier_model_limit).values(
                id=_stable_uuid(f"tier-model:{tier_name}:{model_name}:2026-05"),
                tier_id=tier_id,
                model_name=model_name,
                monthly_requests=monthly_requests,
            )
            bind.execute(
                insert_stmt.on_conflict_do_update(
                    index_elements=[tier_model_limit.c.tier_id, tier_model_limit.c.model_name],
                    set_={"monthly_requests": insert_stmt.excluded.monthly_requests},
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("conversation"):
        op.execute(sa.text("UPDATE conversation SET model = 'gpt-5-nano' WHERE model = 'gpt-5.4-nano'"))
        op.execute(sa.text("UPDATE conversation SET model = 'gpt-5-mini' WHERE model = 'gpt-5.4-mini'"))
        op.execute(sa.text("UPDATE conversation SET model = 'gpt-5.2' WHERE model = 'gpt-5.4'"))
        op.alter_column(
            "conversation",
            "model",
            existing_type=sa.String(),
            server_default=sa.text("'gpt-5-nano'::character varying"),
        )

    if inspector.has_table("request_ledger"):
        op.execute(sa.text("UPDATE request_ledger SET model_name = 'gpt-5-nano' WHERE model_name = 'gpt-5.4-nano'"))
        op.execute(sa.text("UPDATE request_ledger SET model_name = 'gpt-5-mini' WHERE model_name = 'gpt-5.4-mini'"))
        op.execute(sa.text("UPDATE request_ledger SET model_name = 'gpt-5.2' WHERE model_name = 'gpt-5.4'"))
