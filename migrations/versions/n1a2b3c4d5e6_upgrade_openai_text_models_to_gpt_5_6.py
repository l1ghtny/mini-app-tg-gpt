"""upgrade OpenAI text tiers to GPT-5.6

Revision ID: n1a2b3c4d5e6
Revises: l1a2b3c4d5e6
Create Date: 2026-07-10 12:00:00.000000
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert


revision: str = "n1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "l1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SEED_NAMESPACE = uuid.UUID("9c722e9c-bf56-4b39-a671-7a78d2771bdb")
MODEL_REPLACEMENTS = {
    "gpt-5.4-mini": "gpt-5.6-luna",
    "gpt-5.4": "gpt-5.6-terra",
    "gpt-5.5": "gpt-5.6-sol",
}


def _stable_uuid(label: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, label)


def _remap_limits(table_name: str, owner_column: str, value_columns: tuple[str, ...], mapping: dict[str, str]) -> None:
    for old_model, new_model in mapping.items():
        assignments = ", ".join(
            f"{column} = CASE WHEN dst.{column} = -1 OR src.{column} = -1 "
            f"THEN -1 ELSE GREATEST(dst.{column}, src.{column}) END"
            for column in value_columns
        )
        op.execute(sa.text(
            f"""
            UPDATE {table_name} dst
            SET {assignments}
            FROM {table_name} src
            WHERE dst.{owner_column} = src.{owner_column}
              AND dst.model_name = :new_model
              AND src.model_name = :old_model
            """
        ).bindparams(old_model=old_model, new_model=new_model))
        op.execute(sa.text(
            f"""
            DELETE FROM {table_name} src
            USING {table_name} dst
            WHERE src.{owner_column} = dst.{owner_column}
              AND src.model_name = :old_model
              AND dst.model_name = :new_model
            """
        ).bindparams(old_model=old_model, new_model=new_model))
        op.execute(sa.text(
            f"UPDATE {table_name} SET model_name = :new_model WHERE model_name = :old_model"
        ).bindparams(old_model=old_model, new_model=new_model))


def _catalog_table() -> sa.Table:
    return sa.Table(
        "text_model_catalog",
        sa.MetaData(),
        sa.Column("id", sa.Uuid()),
        sa.Column("provider", sa.String()),
        sa.Column("model_name", sa.String()),
        sa.Column("display_name", sa.String()),
        sa.Column("display_name_ru", sa.String()),
        sa.Column("tagline", sa.String()),
        sa.Column("tagline_ru", sa.String()),
        sa.Column("description", sa.String()),
        sa.Column("description_ru", sa.String()),
        sa.Column("best_for", JSONB),
        sa.Column("best_for_ru", JSONB),
        sa.Column("not_great_for", JSONB),
        sa.Column("not_great_for_ru", JSONB),
        sa.Column("speed", sa.String()),
        sa.Column("intelligence", sa.Integer()),
        sa.Column("context_window", sa.Integer()),
        sa.Column("supports", JSONB),
        sa.Column("tier_required", JSONB),
        sa.Column("badges", JSONB),
        sa.Column("credit_cost_hint", sa.Numeric(18, 6)),
        sa.Column("is_active", sa.Boolean()),
        sa.Column("sort_index", sa.Integer()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )


def _pricing_table() -> sa.Table:
    return sa.Table(
        "aimodelpricing",
        sa.MetaData(),
        sa.Column("id", sa.Uuid()),
        sa.Column("provider", sa.String()),
        sa.Column("model_name", sa.String()),
        sa.Column("currency", sa.String()),
        sa.Column("unit_price_input_per_1m", sa.Numeric(18, 6)),
        sa.Column("unit_price_output_per_1m", sa.Numeric(18, 6)),
        sa.Column("unit_price_reasoning_per_1m", sa.Numeric(18, 6)),
        sa.Column("unit_price_web_search_call", sa.Numeric(18, 6)),
        sa.Column("unit_price_image_generation", sa.Numeric(18, 6)),
        sa.Column("is_active", sa.Boolean()),
    )


MODEL_CATALOG = (
    {
        "model_name": "gpt-5.6-luna",
        "display_name": "Smart",
        "display_name_ru": "Умный",
        "tagline": "Fast, capable everyday reasoning",
        "tagline_ru": "Быстрое и умное решение повседневных задач",
        "description": "A strong everyday model for writing, analysis, coding, and tool use.",
        "description_ru": "Сильная повседневная модель для текстов, анализа, кода и инструментов.",
        "best_for": ["Everyday work", "Writing", "Coding"],
        "best_for_ru": ["Повседневные задачи", "Тексты", "Код"],
        "not_great_for": ["Most demanding expert work"],
        "not_great_for_ru": ["Самые сложные экспертные задачи"],
        "speed": "fast",
        "intelligence": 7,
        "tier_required": {"slug": "basic", "min_rank": 1},
        "badges": ["smart"],
        "credit_cost_hint": Decimal("1.500000"),
        "sort_index": 20,
    },
    {
        "model_name": "gpt-5.6-terra",
        "display_name": "Balanced",
        "display_name_ru": "Сбалансированный",
        "tagline": "Deeper reasoning with balanced cost",
        "tagline_ru": "Глубокое мышление при сбалансированной цене",
        "description": "A balanced choice for complex analysis, coding, and long-context tasks.",
        "description_ru": "Сбалансированный выбор для сложного анализа, кода и длинного контекста.",
        "best_for": ["Complex analysis", "Coding", "Long documents"],
        "best_for_ru": ["Сложный анализ", "Код", "Длинные документы"],
        "not_great_for": ["Highest-stakes expert work"],
        "not_great_for_ru": ["Экспертные задачи максимальной сложности"],
        "speed": "medium",
        "intelligence": 9,
        "tier_required": {"slug": "advanced", "min_rank": 2},
        "badges": ["balanced"],
        "credit_cost_hint": Decimal("3.000000"),
        "sort_index": 30,
    },
    {
        "model_name": "gpt-5.6-sol",
        "display_name": "Flagship",
        "display_name_ru": "Флагман",
        "tagline": "Maximum capability for demanding work",
        "tagline_ru": "Максимальные возможности для сложных задач",
        "description": "The flagship model for the hardest reasoning, coding, and professional workflows.",
        "description_ru": "Флагманская модель для самых сложных рассуждений, кода и профессиональных задач.",
        "best_for": ["Expert reasoning", "Hard coding", "Professional workflows"],
        "best_for_ru": ["Экспертный анализ", "Сложный код", "Профессиональные задачи"],
        "not_great_for": ["Simple high-volume tasks"],
        "not_great_for_ru": ["Простые массовые задачи"],
        "speed": "slow",
        "intelligence": 10,
        "tier_required": {"slug": "premium", "min_rank": 3},
        "badges": ["flagship"],
        "credit_cost_hint": Decimal("5.000000"),
        "sort_index": 40,
    },
)

PRICING = {
    "gpt-5.6-luna": (Decimal("1.000000"), Decimal("6.000000")),
    "gpt-5.6-terra": (Decimal("2.500000"), Decimal("15.000000")),
    "gpt-5.6-sol": (Decimal("5.000000"), Decimal("30.000000")),
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("app_user"):
        for old_model, new_model in MODEL_REPLACEMENTS.items():
            op.execute(sa.text(
                "UPDATE app_user SET default_text_model = :new_model WHERE default_text_model = :old_model"
            ).bindparams(old_model=old_model, new_model=new_model))

    if inspector.has_table("conversation"):
        for old_model, new_model in MODEL_REPLACEMENTS.items():
            op.execute(sa.text(
                """
                UPDATE conversation
                SET model = :new_model,
                    last_openai_response_id = NULL,
                    openai_chain_context_fingerprint = NULL
                WHERE model = :old_model
                """
            ).bindparams(old_model=old_model, new_model=new_model))

    if inspector.has_table("tier_model_limit"):
        _remap_limits(
            "tier_model_limit",
            "tier_id",
            ("monthly_requests", "daily_requests"),
            MODEL_REPLACEMENTS,
        )

    if inspector.has_table("usage_pack_model_limit"):
        _remap_limits(
            "usage_pack_model_limit",
            "pack_id",
            ("request_credits",),
            MODEL_REPLACEMENTS,
        )

    if inspector.has_table("text_model_catalog"):
        catalog = _catalog_table()
        now = sa.func.now()
        common = {
            "provider": "OpenAI",
            "context_window": 1_050_000,
            "supports": {
                "vision": True,
                "web_search": True,
                "file_search": True,
                "image_gen": True,
                "reasoning": True,
                "thinking": True,
            },
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        }
        for model in MODEL_CATALOG:
            insert_stmt = pg_insert(catalog).values(
                id=_stable_uuid(f"text-catalog:{model['model_name']}"),
                **common,
                **model,
            )
            bind.execute(insert_stmt.on_conflict_do_update(
                index_elements=[catalog.c.provider, catalog.c.model_name],
                set_={
                    **{key: insert_stmt.excluded[key] for key in model},
                    "context_window": insert_stmt.excluded.context_window,
                    "supports": insert_stmt.excluded.supports,
                    "is_active": True,
                    "updated_at": now,
                },
            ))
        op.execute(sa.text(
            """
            UPDATE text_model_catalog
            SET is_active = FALSE, updated_at = now()
            WHERE provider = 'OpenAI'
              AND model_name IN ('gpt-5.4-mini', 'gpt-5.4', 'gpt-5.5')
            """
        ))

    if inspector.has_table("aimodelpricing"):
        pricing = _pricing_table()
        op.execute(sa.text(
            """
            UPDATE aimodelpricing
            SET is_active = FALSE
            WHERE provider = 'openai'
              AND model_name IN ('gpt-5.4-mini', 'gpt-5.4', 'gpt-5.5')
            """
        ))
        for model_name, (input_price, output_price) in PRICING.items():
            op.execute(sa.text(
                "DELETE FROM aimodelpricing WHERE provider = 'openai' AND model_name = :model_name"
            ).bindparams(model_name=model_name))
            bind.execute(pricing.insert().values(
                id=_stable_uuid(f"pricing:openai:{model_name}"),
                provider="openai",
                model_name=model_name,
                currency="USD",
                unit_price_input_per_1m=input_price,
                unit_price_output_per_1m=output_price,
                unit_price_reasoning_per_1m=output_price,
                unit_price_web_search_call=Decimal("0"),
                unit_price_image_generation=Decimal("0"),
                is_active=True,
            ))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    reverse = {new: old for old, new in MODEL_REPLACEMENTS.items()}

    if inspector.has_table("app_user"):
        for new_model, old_model in reverse.items():
            op.execute(sa.text(
                "UPDATE app_user SET default_text_model = :old_model WHERE default_text_model = :new_model"
            ).bindparams(old_model=old_model, new_model=new_model))

    if inspector.has_table("conversation"):
        for new_model, old_model in reverse.items():
            op.execute(sa.text(
                """
                UPDATE conversation
                SET model = :old_model,
                    last_openai_response_id = NULL,
                    openai_chain_context_fingerprint = NULL
                WHERE model = :new_model
                """
            ).bindparams(old_model=old_model, new_model=new_model))

    if inspector.has_table("tier_model_limit"):
        _remap_limits(
            "tier_model_limit",
            "tier_id",
            ("monthly_requests", "daily_requests"),
            reverse,
        )

    if inspector.has_table("usage_pack_model_limit"):
        _remap_limits(
            "usage_pack_model_limit",
            "pack_id",
            ("request_credits",),
            reverse,
        )

    if inspector.has_table("text_model_catalog"):
        op.execute(sa.text(
            """
            UPDATE text_model_catalog
            SET is_active = FALSE, updated_at = now()
            WHERE provider = 'OpenAI'
              AND model_name IN ('gpt-5.6-luna', 'gpt-5.6-terra', 'gpt-5.6-sol')
            """
        ))
        op.execute(sa.text(
            """
            UPDATE text_model_catalog
            SET is_active = TRUE, updated_at = now()
            WHERE provider = 'OpenAI'
              AND model_name IN ('gpt-5.4-mini', 'gpt-5.4', 'gpt-5.5')
            """
        ))

    if inspector.has_table("aimodelpricing"):
        op.execute(sa.text(
            """
            DELETE FROM aimodelpricing
            WHERE provider = 'openai'
              AND model_name IN ('gpt-5.6-luna', 'gpt-5.6-terra', 'gpt-5.6-sol')
            """
        ))
        op.execute(sa.text(
            """
            UPDATE aimodelpricing
            SET is_active = TRUE
            WHERE provider = 'openai'
              AND model_name IN ('gpt-5.4-mini', 'gpt-5.4', 'gpt-5.5')
            """
        ))
