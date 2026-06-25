"""add_perplexity_sonar_models

Revision ID: l1a2b3c4d5e6
Revises: k1a2b3c4d5e6
Create Date: 2026-06-25 14:30:00.000000

"""
from typing import Sequence, Union
import uuid
from decimal import Decimal

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert


revision: str = "l1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "k1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SEED_NAMESPACE = uuid.UUID("36d38454-dcf3-426a-93de-c132a42db4d6")


def _stable_uuid(label: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, label)


TEXT_MODELS = [
    {
        "provider": "perplexity",
        "model_name": "sonar",
        "display_name": "Perplexity Sonar",
        "display_name_ru": "Perplexity Sonar",
        "tagline": "Fast answers with live web sources",
        "tagline_ru": "Fast answers with live web sources",
        "description": "Best for current facts, quick research, news, prices, and answers that need citations.",
        "description_ru": "Best for current facts, quick research, news, prices, and answers that need citations.",
        "best_for": ["Live web search", "Quick research", "Fact checking"],
        "best_for_ru": ["Live web search", "Quick research", "Fact checking"],
        "not_great_for": ["Image generation", "Document file search", "Private long-form drafting"],
        "not_great_for_ru": ["Image generation", "Document file search", "Private long-form drafting"],
        "speed": "fast",
        "intelligence": 3,
        "context_window": 128000,
        "supports": {
            "vision": False,
            "web_search": True,
            "file_search": False,
            "image_gen": False,
            "reasoning": False,
            "thinking": False,
        },
        "tier_required": {"slug": "basic", "min_rank": 1},
        "badges": ["search"],
        "credit_cost_hint": Decimal("3.0"),
        "sort_index": 80,
    },
    {
        "provider": "perplexity",
        "model_name": "sonar-pro",
        "display_name": "Perplexity Sonar Pro",
        "display_name_ru": "Perplexity Sonar Pro",
        "tagline": "Deeper web research with more sources",
        "tagline_ru": "Deeper web research with more sources",
        "description": "Best for complex research questions, source comparison, and synthesis across multiple references.",
        "description_ru": "Best for complex research questions, source comparison, and synthesis across multiple references.",
        "best_for": ["Deep web research", "Source comparison", "Detailed reports"],
        "best_for_ru": ["Deep web research", "Source comparison", "Detailed reports"],
        "not_great_for": ["Budget-sensitive chats", "Image generation", "Document file search"],
        "not_great_for_ru": ["Budget-sensitive chats", "Image generation", "Document file search"],
        "speed": "medium",
        "intelligence": 4,
        "context_window": 200000,
        "supports": {
            "vision": False,
            "web_search": True,
            "file_search": False,
            "image_gen": False,
            "reasoning": False,
            "thinking": False,
        },
        "tier_required": {"slug": "premium", "min_rank": 3},
        "badges": ["search", "pro"],
        "credit_cost_hint": Decimal("8.0"),
        "sort_index": 90,
    },
]


PRICING = [
    {
        "provider": "perplexity",
        "model_name": "sonar",
        "unit_price_input_per_1m": Decimal("1.0"),
        "unit_price_output_per_1m": Decimal("1.0"),
        "unit_price_reasoning_per_1m": Decimal("0"),
        "unit_price_web_search_call": Decimal("0.005"),
        "unit_price_image_generation": Decimal("0"),
    },
    {
        "provider": "perplexity",
        "model_name": "sonar-pro",
        "unit_price_input_per_1m": Decimal("3.0"),
        "unit_price_output_per_1m": Decimal("15.0"),
        "unit_price_reasoning_per_1m": Decimal("0"),
        "unit_price_web_search_call": Decimal("0.006"),
        "unit_price_image_generation": Decimal("0"),
    },
]


def _text_model_catalog_table(metadata: sa.MetaData) -> sa.Table:
    return sa.Table(
        "text_model_catalog",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
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
    )


def _pricing_table(metadata: sa.MetaData) -> sa.Table:
    return sa.Table(
        "aimodelpricing",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
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


def upgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()

    text_model_catalog = _text_model_catalog_table(metadata)
    ai_model_pricing = _pricing_table(metadata)
    tier_model_limit = sa.Table(
        "tier_model_limit",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tier_id", sa.Uuid()),
        sa.Column("model_name", sa.String()),
        sa.Column("monthly_requests", sa.Integer()),
        sa.Column("daily_requests", sa.Integer()),
    )
    usage_pack_model_limit = sa.Table(
        "usage_pack_model_limit",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("pack_id", sa.Uuid()),
        sa.Column("model_name", sa.String()),
        sa.Column("request_credits", sa.Integer()),
    )

    for row in TEXT_MODELS:
        seed_row = {
            "id": _stable_uuid(f"text-model:{row['provider']}:{row['model_name']}"),
            **row,
            "is_active": True,
        }
        insert_stmt = pg_insert(text_model_catalog).values(**seed_row)
        bind.execute(
            insert_stmt.on_conflict_do_update(
                index_elements=[text_model_catalog.c.provider, text_model_catalog.c.model_name],
                set_={
                    "display_name": insert_stmt.excluded.display_name,
                    "display_name_ru": insert_stmt.excluded.display_name_ru,
                    "tagline": insert_stmt.excluded.tagline,
                    "tagline_ru": insert_stmt.excluded.tagline_ru,
                    "description": insert_stmt.excluded.description,
                    "description_ru": insert_stmt.excluded.description_ru,
                    "best_for": insert_stmt.excluded.best_for,
                    "best_for_ru": insert_stmt.excluded.best_for_ru,
                    "not_great_for": insert_stmt.excluded.not_great_for,
                    "not_great_for_ru": insert_stmt.excluded.not_great_for_ru,
                    "speed": insert_stmt.excluded.speed,
                    "intelligence": insert_stmt.excluded.intelligence,
                    "context_window": insert_stmt.excluded.context_window,
                    "supports": insert_stmt.excluded.supports,
                    "tier_required": insert_stmt.excluded.tier_required,
                    "badges": insert_stmt.excluded.badges,
                    "credit_cost_hint": insert_stmt.excluded.credit_cost_hint,
                    "is_active": insert_stmt.excluded.is_active,
                    "sort_index": insert_stmt.excluded.sort_index,
                },
            )
        )

    for row in PRICING:
        seed_row = {
            "id": _stable_uuid(f"pricing:{row['provider']}:{row['model_name']}"),
            "currency": "USD",
            "is_active": True,
            **row,
        }
        insert_stmt = pg_insert(ai_model_pricing).values(**seed_row)
        bind.execute(
            insert_stmt.on_conflict_do_update(
                index_elements=[ai_model_pricing.c.id],
                set_={
                    "provider": insert_stmt.excluded.provider,
                    "model_name": insert_stmt.excluded.model_name,
                    "currency": insert_stmt.excluded.currency,
                    "unit_price_input_per_1m": insert_stmt.excluded.unit_price_input_per_1m,
                    "unit_price_output_per_1m": insert_stmt.excluded.unit_price_output_per_1m,
                    "unit_price_reasoning_per_1m": insert_stmt.excluded.unit_price_reasoning_per_1m,
                    "unit_price_web_search_call": insert_stmt.excluded.unit_price_web_search_call,
                    "unit_price_image_generation": insert_stmt.excluded.unit_price_image_generation,
                    "is_active": insert_stmt.excluded.is_active,
                },
            )
        )

    tier_rows = bind.execute(sa.text("SELECT id, index FROM subscription_tier")).fetchall()
    source_limits = bind.execute(
        sa.text(
            """
            SELECT tier_id, model_name, monthly_requests, daily_requests
            FROM tier_model_limit
            WHERE model_name IN ('gpt-5.4-mini', 'gpt-5.5')
            """
        )
    ).fetchall()
    source_by_tier_model = {(row[0], row[1]): row for row in source_limits}

    for tier_id, tier_index in tier_rows:
        tier_rank = int(tier_index or 0)
        model_specs = [
            ("sonar", "gpt-5.4-mini", tier_rank >= 1),
            ("sonar-pro", "gpt-5.5", tier_rank >= 3),
        ]
        for target_model, source_model, tier_allowed in model_specs:
            source = source_by_tier_model.get((tier_id, source_model))
            monthly = int(source[2] if source else 0) if tier_allowed else 0
            daily = int(source[3] if source else 0) if tier_allowed else 0
            seed_row = {
                "id": _stable_uuid(f"tier-limit:{tier_id}:{target_model}"),
                "tier_id": tier_id,
                "model_name": target_model,
                "monthly_requests": monthly,
                "daily_requests": daily,
            }
            insert_stmt = pg_insert(tier_model_limit).values(**seed_row)
            bind.execute(
                insert_stmt.on_conflict_do_update(
                    index_elements=[tier_model_limit.c.tier_id, tier_model_limit.c.model_name],
                    set_={
                        "monthly_requests": insert_stmt.excluded.monthly_requests,
                        "daily_requests": insert_stmt.excluded.daily_requests,
                    },
                )
            )

    pack_rows = bind.execute(sa.text("SELECT id FROM usage_pack")).fetchall()
    pack_source_limits = bind.execute(
        sa.text(
            """
            SELECT pack_id, model_name, request_credits
            FROM usage_pack_model_limit
            WHERE model_name IN ('gpt-5.4-mini', 'gpt-5.5')
            """
        )
    ).fetchall()
    pack_source_by_pack_model = {(row[0], row[1]): row for row in pack_source_limits}

    for (pack_id,) in pack_rows:
        for target_model, source_model in (("sonar", "gpt-5.4-mini"), ("sonar-pro", "gpt-5.5")):
            source = pack_source_by_pack_model.get((pack_id, source_model))
            if not source:
                continue
            seed_row = {
                "id": _stable_uuid(f"pack-limit:{pack_id}:{target_model}"),
                "pack_id": pack_id,
                "model_name": target_model,
                "request_credits": source[2],
            }
            insert_stmt = pg_insert(usage_pack_model_limit).values(**seed_row)
            bind.execute(
                insert_stmt.on_conflict_do_update(
                    index_elements=[usage_pack_model_limit.c.pack_id, usage_pack_model_limit.c.model_name],
                    set_={"request_credits": insert_stmt.excluded.request_credits},
                )
            )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM usage_pack_model_limit WHERE model_name IN ('sonar', 'sonar-pro')"))
    op.execute(sa.text("DELETE FROM tier_model_limit WHERE model_name IN ('sonar', 'sonar-pro')"))
    op.execute(sa.text("DELETE FROM aimodelpricing WHERE provider = 'perplexity' AND model_name IN ('sonar', 'sonar-pro')"))
    op.execute(sa.text("DELETE FROM text_model_catalog WHERE provider = 'perplexity' AND model_name IN ('sonar', 'sonar-pro')"))
